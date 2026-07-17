from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ENVIRON_SOURCES = [
    PROJECT_ROOT / ".env",
    PROJECT_ROOT / ".env.local",
]


def resolve_project_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_environs() -> dict[str, str]:
    environs: dict[str, str] = {}
    for source in ENVIRON_SOURCES:
        if not source.exists():
            continue
        environs.update(
            {
                key: value
                for key, value in dotenv_values(source).items()
                if value is not None
            }
        )
    environs.update(os.environ)
    return environs


@dataclass
class QbittorrentConfig:
    host: str
    port: int
    username: str
    password: str

    @classmethod
    def from_env(cls, environs: dict[str, str]) -> QbittorrentConfig:
        try:
            port = int(environs.get("QBIT_PORT", "8080"))
        except ValueError:
            raise ValueError(
                f"QBIT_PORT must be an integer, got {environs.get('QBIT_PORT')}"
            )

        return cls(
            host=environs.get("QBIT_HOST", "localhost"),
            port=port,
            username=environs.get("QBIT_USERNAME", "admin"),
            password=environs.get("QBIT_PASSWORD", "adminadmin"),
        )

    @property
    def base_url(self) -> str:
        return f"{self.host}:{self.port}/api/v2"


@dataclass
class BangumiConfig:
    base_url: str
    user_agent: str
    token: str | None
    username: str | None
    profile_id: str | None

    @classmethod
    def from_env(cls, environs: dict[str, str]) -> BangumiConfig:
        # Support multiple env names: BANGUMI_USERNAME / BANGUMI_USER / BANGUMI_PROFILE_ID / BANGUMI_USER_ID
        username = (
            environs.get("BANGUMI_USERNAME")
            or environs.get("BANGUMI_USER")
            or environs.get("BANGUMI_PROFILE_ID")
            or environs.get("BANGUMI_USER_ID")
        )
        # Keep profile_id alias for backward compat / explicit numeric id
        profile_id = environs.get("BANGUMI_PROFILE_ID") or environs.get("BANGUMI_USER_ID")
        return cls(
            base_url=environs.get("BANGUMI_BASE_URL", "https://api.bgm.tv"),
            user_agent=environs.get("BANGUMI_USER_AGENT", "bonsai-manager/0.1.0"),
            token=environs.get("BANGUMI_TOKEN"),
            username=username,
            profile_id=profile_id or username,
        )


@dataclass
class SearchConfig:
    index_path: Path
    query_cache_path: Path

    embedding_model: str
    embedding_query_prompt_model_marker: str
    embedding_device: str

    @classmethod
    def from_env(cls, environs: dict[str, str]) -> SearchConfig:
        return cls(
            index_path=resolve_project_path(
                Path(environs.get("SEARCH_INDEX_PATH", "aggregate_search_index.json"))
            ),
            query_cache_path=resolve_project_path(
                Path(
                    environs.get(
                        "SEARCH_QUERY_CACHE_PATH",
                        "aggregate_search_query_cache.json",
                    )
                )
            ),
            embedding_model=environs.get(
                "EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"
            ),
            embedding_query_prompt_model_marker=environs.get(
                "EMBEDDING_QUERY_PROMPT_MODEL_MARKER", "Qwen3-Embedding"
            ),
            embedding_device=environs.get("EMBEDDING_DEVICE", "cpu"),
        )


@dataclass
class WebConfig:
    listen: str
    title: str

    @classmethod
    def from_env(cls, environs: dict[str, str]) -> WebConfig:
        return cls(
            listen=environs.get("WEB_LISTEN", "localhost:8000"),
            title=environs.get("WEB_TITLE", "Bonsai Manager"),
        )


@dataclass
class Config:
    db_path: Path
    aggregate_category: str
    audit_categories: tuple[str, ...]
    bangumi: BangumiConfig
    qbittorrent: QbittorrentConfig
    search: SearchConfig
    web: WebConfig

    @classmethod
    def from_env(cls, environs: dict[str, str]) -> Config:
        return cls(
            db_path=resolve_project_path(Path(environs.get("DB_PATH", "db.json"))),
            aggregate_category=environs.get("AGGREGATE_CATEGORY", "anime"),
            audit_categories=tuple(
                category.strip()
                for category in environs.get(
                    "AUDIT_CATEGORIES", "anime,RSS,prowlarr"
                ).split(",")
                if category.strip()
            ),
            bangumi=BangumiConfig.from_env(environs),
            qbittorrent=QbittorrentConfig.from_env(environs),
            search=SearchConfig.from_env(environs),
            web=WebConfig.from_env(environs),
        )


def load_config() -> Config:
    return Config.from_env(load_environs())
