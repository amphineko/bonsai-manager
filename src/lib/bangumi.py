from typing import TypedDict, cast

import requests

from config import BangumiConfig, load_config
from lib.models.bangumi import BangumiSubjectSnapshot


class BangumiSubjectResponse(TypedDict, total=False):
    name: str
    name_cn: str
    type: int


class BangumiClient:
    def __init__(
        self,
        config: BangumiConfig | None = None,
        token: str | None = None,
    ):
        self.config = config or load_config().bangumi
        self.base_url = self.config.base_url.rstrip("/")
        self.token = token if token is not None else self.config.token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": self.config.user_agent,
            }
        )
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    def get_subject(self, subject_id: int) -> BangumiSubjectResponse:
        url = f"{self.base_url}/v0/subjects/{subject_id}"
        response = self.session.get(url)
        response.raise_for_status()
        return cast(BangumiSubjectResponse, response.json())

    def get_subject_snapshot(self, subject_id: int) -> BangumiSubjectSnapshot:
        data = self.get_subject(subject_id)
        return BangumiSubjectSnapshot(
            name=data.get("name", ""),
            name_cn=data.get("name_cn", ""),
            type=data.get("type"),
        )
