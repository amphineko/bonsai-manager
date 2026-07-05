from typing import List

from pydantic import BaseModel, Field


class QbittorrentTorrent(BaseModel):
    hash: str
    name: str = ""
    category: str = ""
    save_path: str = ""


class QbittorrentTorrentFile(BaseModel):
    name: str
    size: int = 0
    progress: float = 0
    priority: int = 0
    is_seed: bool = False


class TorrentMappingLocation(BaseModel):
    hash: str
    aggregates: List[str]


class TrackedTorrentMapping(TorrentMappingLocation):
    torrent: QbittorrentTorrent


class TorrentMappingAudit(BaseModel):
    tracked_found: List[TrackedTorrentMapping] = Field(default_factory=list)
    tracked_missing: List[TorrentMappingLocation] = Field(default_factory=list)
    unmapped: List[QbittorrentTorrent] = Field(default_factory=list)
    duplicates: List[TorrentMappingLocation] = Field(default_factory=list)
