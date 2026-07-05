from typing import List

from pydantic import BaseModel, Field

from lib.models.bangumi import BangumiSubject


class Torrent(BaseModel):
    hash: str


class Aggregate(BaseModel):
    short_name: str
    category: str = "anime"
    bangumi_subjects: List[BangumiSubject] = Field(default_factory=list)
    torrents: List[Torrent] = Field(default_factory=list)


class Database(BaseModel):
    entries: List[Aggregate]
