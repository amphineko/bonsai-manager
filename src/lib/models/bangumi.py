from typing import Optional

from pydantic import BaseModel, Field


class BangumiSubjectSnapshot(BaseModel):
    name: str
    name_cn: str
    type: Optional[int] = None


class BangumiSubject(BaseModel):
    subject_id: int
    last_updated_at: str
    snapshot: Optional[BangumiSubjectSnapshot] = None


class BangumiWishSubject(BaseModel):
    subject_id: int
    name: str = ""
    name_cn: str = ""
    subject_type: int | None = None
    collection_type: int | None = None


class BangumiWishAudit(BaseModel):
    username: str
    wish_total: int = 0
    local_total: int = 0
    missing_count: int = 0
    missing: list[BangumiWishSubject] = Field(default_factory=list)
    existing: list[BangumiWishSubject] = Field(default_factory=list)
