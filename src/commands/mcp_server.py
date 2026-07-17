from typing import TypeVar

from fastmcp import FastMCP

from config import Config, load_config
from lib.db import DBManager
from lib.models import ResponsePayload
from lib.models.aggregates import Aggregate
from lib.models.bangumi import BangumiWishAudit
from lib.models.qbittorrent import TorrentMappingAudit
from lib.models.search import AggregateSearchResults
from lib.search import AggregateSearchManager

mcp = FastMCP("bonsai-manager", version="0.1.0")

DataT = TypeVar("DataT")
_config: Config | None = None


def success(message: str, data: DataT) -> ResponsePayload[DataT]:
    return ResponsePayload(message=message, data=data)


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


@mcp.tool
def add_aggregate(
    short_name: str,
    bangumi_subject_id: int | None = None,
    torrent_hashes: list[str] | None = None,
) -> ResponsePayload[Aggregate]:
    """Add a new aggregate, optionally seeded by Bangumi and torrent hashes."""
    manager = DBManager(get_config())
    aggregate = manager.add_aggregate(short_name, bangumi_subject_id, torrent_hashes)
    return success(
        f"Added aggregate '{aggregate.short_name}'",
        aggregate,
    )


@mcp.tool
def remove_aggregate(short_name: str) -> ResponsePayload[Aggregate]:
    """Remove an aggregate by exact short name."""
    manager = DBManager(get_config())
    aggregate = manager.remove_aggregate(short_name)
    return success(
        f"Removed aggregate '{aggregate.short_name}'",
        aggregate,
    )


@mcp.tool
def update_aggregate_torrents(
    short_name: str,
    add_hashes: list[str] | None = None,
    remove_hashes: list[str] | None = None,
) -> ResponsePayload[list[str]]:
    """Add and/or remove qBittorrent torrent hashes on an existing aggregate."""
    manager = DBManager(get_config())
    torrent_hashes = manager.update_aggregate_torrents(
        short_name,
        add_hashes,
        remove_hashes,
    )
    return success(
        f"Updated torrents for '{short_name}'",
        torrent_hashes,
    )


@mcp.tool
def update_aggregate_bangumi_subjects(
    short_name: str,
    add_subject_ids: list[int] | None = None,
    remove_subject_ids: list[int] | None = None,
) -> ResponsePayload[list[int]]:
    """Add and/or remove Bangumi subject IDs on an existing aggregate."""
    manager = DBManager(get_config())
    subject_ids = manager.update_aggregate_bangumi_subjects(
        short_name,
        add_subject_ids,
        remove_subject_ids,
    )
    return success(
        f"Updated Bangumi subjects for '{short_name}'",
        subject_ids,
    )


@mcp.tool
def list_aggregates(
    filter_short_name: list[str] | None = None,
    filter_torrent_hashes: list[str] | None = None,
    filter_bangumi_subject_name: list[str] | None = None,
    filter_bangumi_subject_cn_name: list[str] | None = None,
) -> ResponsePayload[list[Aggregate]]:
    """List aggregates with deterministic filters.

    Use this when you know exact torrent hashes or wildcard patterns for aggregate
    short names, Bangumi original names, or Bangumi Chinese names. At least one
    filter argument is required. For natural-language discovery, use
    search_aggregates instead.
    """
    manager = DBManager(get_config())
    aggregates = manager.list_aggregates(
        filter_short_name,
        filter_torrent_hashes,
        filter_bangumi_subject_name,
        filter_bangumi_subject_cn_name,
    )
    return success(
        "Listed aggregates",
        aggregates,
    )


@mcp.tool
def search_aggregates(
    query: str,
    limit: int = 10,
    threshold: float | None = None,
) -> ResponsePayload[AggregateSearchResults]:
    """Search aggregates by natural-language text and return scored results.

    Use this for fuzzy semantic discovery, vague descriptions, translated title
    fragments, or cross-language title search. For exact torrent hashes or known
    wildcard patterns, use list_aggregates instead.
    """
    config = get_config()
    manager = DBManager(config)
    search_manager = AggregateSearchManager(config.search)
    try:
        results = search_manager.search(
            manager.load_db(),
            query,
            limit=limit,
            threshold=threshold,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Semantic search requires sentence-transformers and torch. "
            "Install them with `uv add sentence-transformers torch`."
        ) from exc
    return success(
        "Searched aggregates",
        AggregateSearchResults(results=results),
    )


@mcp.tool
def audit_torrent_mapping(
    category: list[str] | None = None,
) -> ResponsePayload[TorrentMappingAudit]:
    """Audit qBittorrent torrents against stored aggregate mappings."""
    config = get_config()
    categories = category or list(config.audit_categories)
    manager = DBManager(config)
    return success(
        "Audited torrent mapping",
        manager.audit_torrent_mapping(categories),
    )


@mcp.tool
def audit_bangumi_wish(
    username: str | None = None,
    collection_type: int = 1,
) -> ResponsePayload[BangumiWishAudit]:
    """Audit Bangumi Wish vs local catalog: which Wish anime are not yet downloaded.

    Args:
        username: Bangumi username or user ID. If not provided, uses BANGUMI_USERNAME / BANGUMI_PROFILE_ID from env.
        collection_type: 1=Wish (想看), 3=Doing (在看), 4=On Hold, etc. Default 1.

    Returns:
        Wish audit with missing = Wish but not in local DB.
    """
    config = get_config()
    target_user = username or config.bangumi.username or config.bangumi.profile_id
    if not target_user:
        raise ValueError(
            "No Bangumi username provided. Set BANGUMI_USERNAME or BANGUMI_PROFILE_ID in .env, or pass username param."
        )
    manager = DBManager(config)
    audit = manager.audit_bangumi_wish(target_user, collection_type=collection_type)
    return success(
        f"Audited Bangumi wish for '{target_user}': {audit.missing_count} missing / {audit.wish_total} wish",
        audit,
    )


@mcp.tool
def get_bangumi_user_collections(
    username: str | None = None,
    subject_type: int = 2,
    collection_type: int | None = 1,
    limit: int = 50,
    offset: int = 0,
) -> ResponsePayload[dict]:
    """Get raw Bangumi user collections for debugging/inspection."""
    config = get_config()
    target_user = username or config.bangumi.username or config.bangumi.profile_id
    if not target_user:
        raise ValueError("No Bangumi username provided.")
    from lib.bangumi import BangumiClient

    client = BangumiClient(config.bangumi)
    data = client.get_user_collections(
        target_user, subject_type=subject_type, collection_type=collection_type, limit=limit, offset=offset
    )
    return success(f"Fetched collections for '{target_user}'", data)


def run_mcp_server(config: Config | None = None) -> None:
    global _config
    _config = config or load_config()
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    run_mcp_server()
