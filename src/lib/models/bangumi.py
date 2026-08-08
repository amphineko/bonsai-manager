from enum import IntEnum

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
    DONE = 2
    DOING = 3
    ON_HOLD = 4
    DROPPED = 5


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


class BangumiCollectionSyncState(BaseModel):
    username: str
    last_successful_sync_at: AwareDatetime
