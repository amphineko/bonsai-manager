from typing import Optional

from pydantic import BaseModel


class BangumiSubjectSnapshot(BaseModel):
    name: str
    name_cn: str
    type: Optional[int] = None


class BangumiSubject(BaseModel):
    subject_id: int
    last_updated_at: str
    snapshot: Optional[BangumiSubjectSnapshot] = None
