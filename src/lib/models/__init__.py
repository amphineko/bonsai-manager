from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

from lib.models.aggregates import Aggregate, Database, Torrent
from lib.models.bangumi import BangumiSubject, BangumiSubjectSnapshot
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
    SearchQueryCache,
    SearchQueryCacheEntry,
    SearchIndex,
)


DataT = TypeVar("DataT")


class ResponsePayload(BaseModel, Generic[DataT]):
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
    "Database",
    "QbittorrentTorrent",
    "QbittorrentTorrentFile",
    "ResponsePayload",
    "SearchIndex",
    "SearchQueryCache",
    "SearchQueryCacheEntry",
    "Torrent",
    "TorrentMappingAudit",
    "TorrentMappingLocation",
    "TrackedTorrentMapping",
]
