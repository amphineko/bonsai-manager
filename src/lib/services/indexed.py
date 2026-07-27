from __future__ import annotations

from dataclasses import replace

from config import Config, SearchConfig
from lib.models.aggregates import Aggregate, Torrent
from lib.models.health import HealthCheckReport, SearchIndexConsistencyCheck
from lib.models.qbittorrent import TorrentMappingAudit
from lib.models.search import AggregateSearchDocument, AggregateSearchResult
from lib.search import AggregateSearchManager
from lib.search.repositories import LanceDbSearchRepository
from lib.services.aggregates import AggregateService
from lib.sql.repositories import SqliteAggregateRepository


class IndexedAggregateService:
    def __init__(
        self,
        aggregates: AggregateService,
        search: AggregateSearchManager,
    ):
        self.aggregates = aggregates
        self.search = search

    def close(self) -> None:
        self.aggregates.close()

    @classmethod
    def from_config(
        cls,
        config: Config,
        search_config: SearchConfig | None = None,
        local_files_only: bool = True,
        embedding_device: str | None = None,
    ) -> IndexedAggregateService:
        aggregate_service = AggregateService(config)
        selected_search_config = search_config or config.search
        if embedding_device:
            selected_search_config = replace(
                selected_search_config,
                embedding_device=embedding_device,
            )
        search_manager = AggregateSearchManager(
            selected_search_config,
            aggregates=aggregate_service.queries,
            local_files_only=local_files_only,
        )
        return cls(aggregate_service, search_manager)

    @classmethod
    def check_health_from_config(cls, config: Config) -> HealthCheckReport:
        aggregate_repository: SqliteAggregateRepository | None = None
        if config.database.path.is_file():
            aggregate_repository = SqliteAggregateRepository(
                config.database.path,
                create=False,
            )
        sqlite_ready = (
            aggregate_repository is not None and aggregate_repository.schema_is_ready()
        )
        search_repository = LanceDbSearchRepository(config.search)
        lancedb_ready = search_repository.documents_table_exists()
        if sqlite_ready and lancedb_ready:
            if aggregate_repository is None:
                raise AssertionError("Ready SQLite repository was not initialized.")
            aggregate_service = AggregateService(
                config,
                repository=aggregate_repository,
            )
            return cls(
                aggregate_service,
                AggregateSearchManager(
                    config.search,
                    aggregates=aggregate_service.queries,
                    repository=search_repository,
                ),
            ).check_health()

        aggregate_count = 0
        if sqlite_ready:
            if aggregate_repository is None:
                raise AssertionError("Ready SQLite repository was not initialized.")
            aggregate_service = AggregateService(
                config,
                repository=aggregate_repository,
            )
            aggregate_count = aggregate_service.count_aggregates()

        return HealthCheckReport(
            healthy=False,
            checks=[
                SearchIndexConsistencyCheck(
                    healthy=False,
                    sqlite_ready=sqlite_ready,
                    lancedb_ready=lancedb_ready,
                    aggregate_count=aggregate_count,
                    document_count=(
                        search_repository.count_documents() if lancedb_ready else 0
                    ),
                    missing_documents=[],
                    orphaned_documents=[],
                    stale_documents=[],
                    duplicate_documents=[],
                )
            ],
        )

    def add_aggregate(
        self,
        short_name: str,
        bangumi_subject_id: int | None = None,
        torrent_hashes: list[str] | None = None,
    ) -> Aggregate:
        aggregate = self.aggregates.add_aggregate(
            short_name,
            bangumi_subject_id,
            torrent_hashes,
        )
        self.search.index_aggregate(aggregate)
        return aggregate

    def remove_aggregate(self, short_name: str) -> Aggregate:
        aggregate = self.aggregates.remove_aggregate(short_name)
        self.search.delete_aggregate(short_name)
        return aggregate

    def update_aggregate_torrents(
        self,
        short_name: str,
        group: str | None = None,
        add_hashes: list[str] | None = None,
        remove_hashes: list[str] | None = None,
    ) -> dict[str, list[str]]:
        return self.aggregates.update_aggregate_torrents(
            short_name=short_name,
            group=group,
            add_hashes=add_hashes,
            remove_hashes=remove_hashes,
        )

    def update_aggregate_bangumi_subjects(
        self,
        short_name: str,
        add_subject_ids: list[int] | None = None,
        remove_subject_ids: list[int] | None = None,
    ) -> list[int]:
        subject_ids = self.aggregates.update_aggregate_bangumi_subjects(
            short_name,
            add_subject_ids,
            remove_subject_ids,
        )
        aggregates = self.aggregates.queries.get_by_short_names([short_name])
        if aggregates:
            self.search.index_aggregate(aggregates[0])
        return subject_ids

    def list_aggregates(
        self,
        filter_short_name: list[str] | None = None,
        filter_torrent_hashes: list[str] | None = None,
        filter_bangumi_subject_name: list[str] | None = None,
        filter_bangumi_subject_cn_name: list[str] | None = None,
    ) -> list[Aggregate]:
        return self.aggregates.list_aggregates(
            filter_short_name,
            filter_torrent_hashes,
            filter_bangumi_subject_name,
            filter_bangumi_subject_cn_name,
        )

    def count_aggregates(self) -> int:
        return self.aggregates.count_aggregates()

    def check_health(self) -> HealthCheckReport:
        checks = [self.search.check_consistency()]
        return HealthCheckReport(
            healthy=all(check.healthy for check in checks),
            checks=checks,
        )

    def search_aggregates(
        self,
        query: str,
        limit: int = 10,
        threshold: float | None = None,
    ) -> list[AggregateSearchResult]:
        return self.search.search(query, limit=limit, threshold=threshold)

    def rebuild_search_index(
        self,
        force: bool = False,
        show_progress: bool = False,
    ) -> list[AggregateSearchDocument]:
        return self.search.rebuild(force=force, show_progress=show_progress)

    def get_torrent_display_path(self, torrent: Torrent) -> str:
        return self.aggregates.get_torrent_display_path(torrent)

    def audit_torrent_mapping(
        self,
        categories: list[str] | None = None,
    ) -> TorrentMappingAudit:
        return self.aggregates.audit_torrent_mapping(categories)
