from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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
    url: str
    username: str
    password: str

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.url)
        except ValueError as exc:
            raise ValueError("QBIT_URL is not a valid URL.") from exc
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("QBIT_URL must use the http or https scheme.")
        if parsed.hostname is None:
            raise ValueError("QBIT_URL must include a hostname.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("QBIT_URL must not include credentials.")
        if parsed.path not in {"", "/"}:
            raise ValueError("QBIT_URL must not include a path.")
        if parsed.query or parsed.fragment:
            raise ValueError("QBIT_URL must not include a query or fragment.")
        if any(character.isspace() for character in parsed.netloc):
            raise ValueError("QBIT_URL must not include whitespace.")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("QBIT_URL contains an invalid port.") from exc

        self.url = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    @classmethod
    def from_env(cls, environs: dict[str, str]) -> QbittorrentConfig:
        return cls(
            url=environs.get("QBIT_URL", "http://localhost:8080"),
            username=environs.get("QBIT_USERNAME", "admin"),
            password=environs.get("QBIT_PASSWORD", "adminadmin"),
        )

    @property
    def base_url(self) -> str:
        return f"{self.url}/api/v2"


@dataclass
class BangumiConfig:
    base_url: str
    user_agent: str
    token: str | None
    username: str | None
    collection_ttl: timedelta

    @classmethod
    def from_env(cls, environs: dict[str, str]) -> BangumiConfig:
        try:
            collection_ttl_seconds = int(
                environs.get("BANGUMI_COLLECTION_TTL_SECONDS", "21600")
            )
        except ValueError as exc:
            raise ValueError(
                "BANGUMI_COLLECTION_TTL_SECONDS must be an integer, got "
                f"{environs.get('BANGUMI_COLLECTION_TTL_SECONDS')!r}"
            ) from exc
        if collection_ttl_seconds < 0:
            raise ValueError("BANGUMI_COLLECTION_TTL_SECONDS cannot be negative.")

        username = environs.get("BANGUMI_USERNAME", "").strip() or None
        return cls(
            base_url=environs.get("BANGUMI_BASE_URL", "https://api.bgm.tv"),
            user_agent=environs.get("BANGUMI_USER_AGENT", "bonsai-manager/0.1.0"),
            token=environs.get("BANGUMI_TOKEN"),
            username=username,
            collection_ttl=timedelta(seconds=collection_ttl_seconds),
        )


@dataclass
class SearchConfig:
    lancedb_path: Path

    embedding_model: str
    embedding_query_prompt_model_marker: str
    embedding_device: str

    @classmethod
    def from_env(cls, environs: dict[str, str]) -> SearchConfig:
        return cls(
            lancedb_path=resolve_project_path(
                Path(environs.get("SEARCH_LANCEDB_PATH", "aggregate_search.lancedb"))
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
class DatabaseConfig:
    path: Path

    @classmethod
    def from_env(cls, environs: dict[str, str]) -> DatabaseConfig:
        return cls(
            path=resolve_project_path(Path(environs.get("DB_PATH", "db.sqlite3"))),
        )


@dataclass
class Config:
    database: DatabaseConfig
    aggregate_category: str
    audit_checks: tuple[str, ...]
    audit_categories: tuple[str, ...]
    bangumi: BangumiConfig
    qbittorrent: QbittorrentConfig
    search: SearchConfig
    web: WebConfig

    @classmethod
    def from_env(cls, environs: dict[str, str]) -> Config:
        return cls(
            database=DatabaseConfig.from_env(environs),
            aggregate_category=environs.get("AGGREGATE_CATEGORY", "anime"),
            audit_checks=tuple(
                check.strip()
                for check in environs.get("AUDIT_CHECKS", "torrent_mapping").split(",")
                if check.strip()
            ),
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
