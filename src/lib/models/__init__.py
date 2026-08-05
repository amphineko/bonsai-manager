from typing import Literal

from pydantic import BaseModel

from lib.models.aggregates import Aggregate, Database, Torrent
from lib.models.audit import (
    AuditCheckResult,
    AuditCheckStatus,
    AuditFinding,
    AuditReport,
    AuditSeverity,
)
from lib.models.bangumi import (
    BangumiCollectionSyncState,
    BangumiCollectionType,
    BangumiSubject,
    BangumiSubjectSnapshot,
    BangumiTag,
    BangumiUserCollection,
)
from lib.models.health import HealthCheckReport, SearchIndexConsistencyCheck
from lib.models.qbittorrent import (
    QbittorrentTorrent,
    QbittorrentTorrentFile,
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
    AuditSyncResult,
    BangumiCollectionSyncResult,
    SearchIndexSyncResult,
    SyncReport,
    SyncStepResult,
    SyncStepStatus,
)


class ResponsePayload[DataT](BaseModel):
    status: Literal["success", "error"] = "success"
    message: str
    data: DataT


__all__ = [
    "Aggregate",
    "AggregateSearchDocument",
    "AggregateSearchDocumentMetadata",
    "AggregateSearchResult",
    "AggregateSearchResults",
    "AuditCheckResult",
    "AuditCheckStatus",
    "AuditFinding",
    "AuditReport",
    "AuditSeverity",
    "AuditSyncResult",
    "BangumiCollectionSyncResult",
    "BangumiCollectionSyncState",
    "BangumiCollectionType",
    "BangumiSubject",
    "BangumiSubjectSnapshot",
    "BangumiTag",
    "BangumiUserCollection",
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
]
