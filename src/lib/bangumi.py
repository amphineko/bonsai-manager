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

    def get_user_collections(
        self,
        username: str,
        subject_type: int = 2,
        collection_type: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Fetch a user's collections (e.g. Wish/Collect/Doing).

        Maps to GET /v0/users/{username}/collections
        subject_type: 2=anime, collection_type: 1=Wish, 2=Collect, 3=Doing, 4=On Hold, 5=Dropped
        """
        url = f"{self.base_url}/v0/users/{username}/collections"
        params: dict[str, int] = {
            "limit": limit,
            "offset": offset,
        }
        if subject_type is not None:
            params["subject_type"] = subject_type
        if collection_type is not None:
            params["type"] = collection_type
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def get_all_user_collections(
        self,
        username: str,
        subject_type: int = 2,
        collection_type: int | None = None,
        page_size: int = 100,
    ) -> list[dict]:
        """Paginate through all collections for a user."""
        all_items: list[dict] = []
        offset = 0
        while True:
            data = self.get_user_collections(
                username, subject_type, collection_type, limit=page_size, offset=offset
            )
            items = data.get("data", [])
            if not items:
                break
            all_items.extend(items)
            total = data.get("total", 0)
            if len(all_items) >= total:
                break
            offset += page_size
        return all_items
