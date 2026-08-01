from typing import Literal

from pydantic import BaseModel

from lib.models.aggregates import Aggregate, Database, Torrent
from lib.models.bangumi import BangumiSubject, BangumiSubjectSnapshot, BangumiTag
from lib.models.health import HealthCheckReport, SearchIndexConsistencyCheck
from lib.models.qbittorrent import (
    QbittorrentTorrent,
    QbittorrentTorrentFile,
    TorrentMappingAudit,
    TorrentMappingLocation,
    TrackedTorrentMapping,
)
from lib.models.search import (
    AggregateSearchDocument,
    AggregateSearchDocumentMetadata,
    AggregateSearchResult,
    AggregateSearchResults,
    SearchDocumentMatch,
    SearchIndexRebuildResult,
    SearchQueryCache,
    SearchQueryCacheEntry,
)
from lib.models.sync import (
    SearchIndexSyncResult,
    SyncReport,
    SyncStepResult,
    SyncStepStatus,
    TorrentAuditSyncResult,
)


class ResponsePayload[DataT](BaseModel):
    status: Literal["success"] = "success"
    message: str
    data: DataT


__all__ = [
    "Aggregate",
    "AggregateSearchDocument",
    "AggregateSearchDocumentMetadata",
    "AggregateSearchResult",
    "AggregateSearchResults",
    "BangumiSubject",
    "BangumiSubjectSnapshot",
    "BangumiTag",
    "Database",
    "HealthCheckReport",
    "QbittorrentTorrent",
    "QbittorrentTorrentFile",
    "ResponsePayload",
    "SearchDocumentMatch",
    "SearchIndexConsistencyCheck",
    "SearchIndexRebuildResult",
    "SearchIndexSyncResult",
    "SearchQueryCache",
    "SearchQueryCacheEntry",
    "SyncReport",
    "SyncStepResult",
    "SyncStepStatus",
    "Torrent",
    "TorrentAuditSyncResult",
    "TorrentMappingAudit",
    "TorrentMappingLocation",
    "TrackedTorrentMapping",
]
