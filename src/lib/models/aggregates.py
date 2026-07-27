from pydantic import BaseModel, Field

from lib.models.bangumi import BangumiSubject


class Torrent(BaseModel):
    hash: str


class Aggregate(BaseModel):
    short_name: str
    category: str = "anime"
    bangumi_subjects: list[BangumiSubject] = Field(default_factory=list)
    torrents: list[Torrent] = Field(default_factory=list)


class Database(BaseModel):
    entries: list[Aggregate]
