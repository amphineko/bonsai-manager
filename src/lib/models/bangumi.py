from enum import IntEnum, StrEnum

from pydantic import AwareDatetime, BaseModel, Field


class BangumiTag(BaseModel):
    name: str
    count: int = 0


class BangumiSubjectSnapshot(BaseModel):
    name: str
    name_cn: str
    type: int | None = None
    tags: list[BangumiTag] = Field(default_factory=list)


class BangumiSubject(BaseModel):
    subject_id: int
    last_updated_at: str
    snapshot: BangumiSubjectSnapshot | None = None


class BangumiCollectionType(IntEnum):
    WISH = 1
    COLLECT = 2
    DONE = 2
    DOING = 3
    ON_HOLD = 4
    DROPPED = 5


class BangumiCollectionLocalState(StrEnum):
    UNMAPPED = "unmapped"
    EMPTY = "empty"
    WITH_TORRENTS = "with_torrents"


DEFAULT_BANGUMI_COLLECTION_TYPES = (
    BangumiCollectionType.WISH,
    BangumiCollectionType.DOING,
)
DEFAULT_BANGUMI_COLLECTION_LOCAL_STATES = (
    BangumiCollectionLocalState.UNMAPPED,
    BangumiCollectionLocalState.EMPTY,
)


class BangumiRemoteCollection(BaseModel):
    subject_id: int
    subject_type: int
    type: BangumiCollectionType
    updated_at: AwareDatetime
    subject: BangumiSubjectSnapshot | None = None


class BangumiCollectionPage(BaseModel):
    total: int = 0
    limit: int = 0
    offset: int = 0
    data: list[BangumiRemoteCollection] = Field(default_factory=list)


class BangumiUserCollection(BaseModel):
    username: str
    subject_id: int
    collection_type: BangumiCollectionType
    remote_updated_at: AwareDatetime
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    synced_at: AwareDatetime
    removed_at: AwareDatetime | None = None


class BangumiCollectionAggregateCoverage(BaseModel):
    short_name: str
    torrent_count: int


class BangumiCollectionSubjectCoverage(BaseModel):
    subject: BangumiSubject
    collection_type: BangumiCollectionType
    local_state: BangumiCollectionLocalState
    aggregates: list[BangumiCollectionAggregateCoverage] = Field(default_factory=list)
    torrent_count: int


class BangumiCollectionSyncState(BaseModel):
    username: str
    last_successful_sync_at: AwareDatetime
