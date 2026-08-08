from typing import TypedDict, cast

import requests

from config import BangumiConfig, load_config
from lib.models.bangumi import (
    BangumiCollectionPage,
    BangumiRemoteCollection,
    BangumiSubjectSnapshot,
    BangumiTag,
)

COLLECTION_PAGE_LIMIT = 50
ANIME_SUBJECT_TYPE = 2


class BangumiTagResponse(TypedDict, total=False):
    name: str
    count: int


class BangumiSubjectResponse(TypedDict, total=False):
    name: str
    name_cn: str
    type: int
    tags: list[BangumiTagResponse]


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

    def close(self) -> None:
        self.session.close()

    def get_subject(self, subject_id: int) -> BangumiSubjectResponse:
        url = f"{self.base_url}/v0/subjects/{subject_id}"
        response = self.session.get(url)
        response.raise_for_status()
        return cast("BangumiSubjectResponse", response.json())

    def get_subject_snapshot(self, subject_id: int) -> BangumiSubjectSnapshot:
        data = self.get_subject(subject_id)
        return BangumiSubjectSnapshot(
            name=data.get("name", ""),
            name_cn=data.get("name_cn", ""),
            type=data.get("type"),
            tags=[
                BangumiTag(name=tag.get("name", ""), count=tag.get("count", 0))
                for tag in data.get("tags", [])
                if tag.get("name")
            ],
        )

    def get_user_collections(self, username: str) -> list[BangumiRemoteCollection]:
        collections: list[BangumiRemoteCollection] = []
        seen_subject_ids: set[int] = set()
        offset = 0
        total: int | None = None

        while total is None or offset < total:
            response = self.session.get(
                f"{self.base_url}/v0/users/{username}/collections",
                params={
                    "subject_type": ANIME_SUBJECT_TYPE,
                    "limit": COLLECTION_PAGE_LIMIT,
                    "offset": offset,
                },
            )
            response.raise_for_status()
            page = BangumiCollectionPage.model_validate(response.json())
            if page.offset != offset:
                raise ValueError(
                    f"Bangumi collection page returned offset {page.offset}; "
                    f"expected {offset}."
                )
            if total is None:
                total = page.total
            elif page.total != total:
                raise ValueError(
                    "Bangumi collection total changed during pagination; retry sync."
                )
            if not page.data and offset < total:
                raise ValueError("Bangumi collection pagination ended before total.")

            for collection in page.data:
                if collection.subject_type != ANIME_SUBJECT_TYPE:
                    raise ValueError(
                        "Bangumi anime collection response contained subject type "
                        f"{collection.subject_type}."
                    )
                if collection.subject_id in seen_subject_ids:
                    raise ValueError(
                        "Bangumi collection pagination returned duplicate subject ID "
                        f"{collection.subject_id}."
                    )
                seen_subject_ids.add(collection.subject_id)
                if collection.subject is None:
                    collection.subject = self.get_subject_snapshot(
                        collection.subject_id
                    )
                collections.append(collection)
            offset += len(page.data)

        if total is not None and len(collections) != total:
            raise ValueError(
                f"Bangumi collection returned {len(collections)} records; "
                f"expected {total}."
            )
        return collections
