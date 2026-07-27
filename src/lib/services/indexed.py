from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from lib.search import AggregateSearchManager
from lib.services.aggregates import AggregateService

if TYPE_CHECKING:
    from config import Config, SearchConfig
    from lib.models.aggregates import Aggregate, Torrent
    from lib.models.qbittorrent import TorrentMappingAudit
    from lib.models.search import AggregateSearchDocument, AggregateSearchResult


class IndexedAggregateService:
    def __init__(
        self,
        aggregates: AggregateService,
        search: AggregateSearchManager,
    ):
        self.aggregates = aggregates
        self.search = search

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
        add_hashes: list[str] | None = None,
        remove_hashes: list[str] | None = None,
    ) -> list[str]:
        return self.aggregates.update_aggregate_torrents(
            short_name,
            add_hashes,
            remove_hashes,
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
