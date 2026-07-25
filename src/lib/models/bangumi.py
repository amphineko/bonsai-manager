from typing import Optional

from pydantic import BaseModel, Field


class BangumiTag(BaseModel):
    name: str
    count: int = 0


class BangumiSubjectSnapshot(BaseModel):
    name: str
    name_cn: str
    type: Optional[int] = None
    tags: list[BangumiTag] = Field(default_factory=list)


class BangumiSubject(BaseModel):
    subject_id: int
    last_updated_at: str
    snapshot: Optional[BangumiSubjectSnapshot] = None
