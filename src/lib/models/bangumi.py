from pydantic import BaseModel, Field


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
