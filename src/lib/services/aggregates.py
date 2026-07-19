from typing import List

from config import Config, load_config
from lib.bangumi import BangumiClient
from lib.models.aggregates import Aggregate, Torrent
from lib.models.qbittorrent import TorrentMappingAudit
from lib.qbittorrent import QbittorrentClient
from lib.services.audit import AggregateAuditService
from lib.services.bangumi import AggregateBangumiService
from lib.services.creation import AggregateCreationService
from lib.services.queries import AggregateQueryService
from lib.services.torrents import AggregateTorrentService
from lib.sql.repositories import SqliteAggregateRepository


class AggregateService:
    def __init__(
        self,
        config: Config | None = None,
        repository: SqliteAggregateRepository | None = None,
    ):
        self.config = config or load_config()
        self.repository = repository or SqliteAggregateRepository(
            self.config.database.path
        )
        self.qbit = QbittorrentClient(self.config.qbittorrent)
        self.bangumi_client = BangumiClient(self.config.bangumi)
        self.queries = AggregateQueryService(self.repository, self.qbit)
        self.torrents = AggregateTorrentService(self.repository, self.qbit)
        self.bangumi = AggregateBangumiService(self.repository, self.bangumi_client)
        self.creation = AggregateCreationService(
            self.repository,
            self.torrents,
            self.bangumi,
            self.config.aggregate_category,
        )
        self.audit = AggregateAuditService(
            self.repository,
            self.qbit,
            self.config.audit_categories,
        )

    def add_aggregate(
        self,
        short_name: str,
        bangumi_subject_id: int | None = None,
        torrent_hashes: List[str] | None = None,
    ) -> Aggregate:
        return self.creation.add_aggregate(
            short_name,
            bangumi_subject_id,
            torrent_hashes,
        )

    def remove_aggregate(self, short_name: str) -> Aggregate:
        return self.queries.remove_aggregate(short_name)

    def update_aggregate_torrents(
        self,
        short_name: str,
        add_hashes: List[str] | None = None,
        remove_hashes: List[str] | None = None,
    ) -> List[str]:
        return self.torrents.update_aggregate_torrents(
            short_name,
            add_hashes,
            remove_hashes,
        )

    def update_aggregate_bangumi_subjects(
        self,
        short_name: str,
        add_subject_ids: List[int] | None = None,
        remove_subject_ids: List[int] | None = None,
    ) -> List[int]:
        return self.bangumi.update_aggregate_bangumi_subjects(
            short_name,
            add_subject_ids,
            remove_subject_ids,
        )

    def list_aggregates(
        self,
        filter_short_name: List[str] | None = None,
        filter_torrent_hashes: List[str] | None = None,
        filter_bangumi_subject_name: List[str] | None = None,
        filter_bangumi_subject_cn_name: List[str] | None = None,
    ) -> List[Aggregate]:
        return self.queries.list_aggregates(
            filter_short_name,
            filter_torrent_hashes,
            filter_bangumi_subject_name,
            filter_bangumi_subject_cn_name,
        )

    def get_torrent_display_path(self, torrent: Torrent) -> str:
        return self.queries.get_torrent_display_path(torrent)

    def audit_torrent_mapping(
        self,
        categories: List[str] | None = None,
    ) -> TorrentMappingAudit:
        return self.audit.audit_torrent_mapping(categories)


DBManager = AggregateService
