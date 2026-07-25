from typing import Literal

from pydantic import BaseModel

from lib.models.aggregates import Aggregate, Database, Torrent
from lib.models.bangumi import BangumiSubject, BangumiSubjectSnapshot, BangumiTag
from lib.models.qbittorrent import (
    QbittorrentTorrent,
    QbittorrentTorrentFile,
    TorrentMappingAudit,
    TorrentMappingLocation,
    TrackedTorrentMapping,
)
from lib.models.search import (
    AggregateSearchDocument,
    AggregateSearchResult,
    AggregateSearchResults,
    SearchDocumentMatch,
    SearchQueryCache,
    SearchQueryCacheEntry,
)


class ResponsePayload[DataT](BaseModel):
    status: Literal["success"] = "success"
    message: str
    data: DataT


__all__ = [
    "Aggregate",
    "AggregateSearchDocument",
    "AggregateSearchResult",
    "AggregateSearchResults",
    "BangumiSubject",
    "BangumiSubjectSnapshot",
    "BangumiTag",
    "Database",
    "QbittorrentTorrent",
    "QbittorrentTorrentFile",
    "ResponsePayload",
    "SearchDocumentMatch",
    "SearchQueryCache",
    "SearchQueryCacheEntry",
    "Torrent",
    "TorrentMappingAudit",
    "TorrentMappingLocation",
    "TrackedTorrentMapping",
]
