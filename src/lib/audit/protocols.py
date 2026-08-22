from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from lib.models.bangumi import (
    BangumiCollectionLocalState,
    BangumiCollectionSubjectCoverage,
    BangumiCollectionType,
)
from lib.models.qbittorrent import QbittorrentTorrent, QbittorrentTorrentFile

if TYPE_CHECKING:
    from lib.audit.context import AuditContext
    from lib.models.audit import AuditFinding


class AggregateAuditor(Protocol):
    name: str

    def audit(self, context: AuditContext) -> list[AuditFinding]: ...


class AuditQbittorrentClient(Protocol):
    def login(self) -> None: ...

    def get_all_torrents(self) -> list[QbittorrentTorrent]: ...

    def get_torrent_files(
        self,
        torrent_hash: str,
    ) -> list[QbittorrentTorrentFile]: ...


class CollectionCoverageProvider(Protocol):
    def list_subject_coverage(
        self,
        collection_types: tuple[BangumiCollectionType, ...],
        local_states: tuple[BangumiCollectionLocalState, ...],
    ) -> list[BangumiCollectionSubjectCoverage]: ...
