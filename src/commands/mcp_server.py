from __future__ import annotations

from fastmcp import FastMCP

from config import Config, load_config
from lib.models import ResponsePayload
from lib.models.aggregates import Aggregate
from lib.models.health import HealthCheckReport
from lib.models.qbittorrent import TorrentMappingAudit
from lib.models.search import AggregateSearchResults, SearchIndexRebuildResult
from lib.services import IndexedAggregateService

mcp = FastMCP("bonsai-manager", version="0.1.0")

_config: Config | None = None


def success[DataT](message: str, data: DataT) -> ResponsePayload[DataT]:
    return ResponsePayload(message=message, data=data)


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


@mcp.resource("bonsai://aggregates/")
def aggregate_summary() -> dict[str, int]:
    """Return a summary of the tracked aggregate collection."""
    manager = IndexedAggregateService.from_config(get_config())
    return {"total": manager.count_aggregates()}


@mcp.tool
def add_aggregate(
    short_name: str,
    bangumi_subject_id: int | None = None,
    torrent_hashes: list[str] | None = None,
) -> ResponsePayload[Aggregate]:
    """Add a new aggregate, optionally seeded by Bangumi and torrent hashes."""
    manager = IndexedAggregateService.from_config(get_config())
    aggregate = manager.add_aggregate(short_name, bangumi_subject_id, torrent_hashes)
    return success(
        f"Added aggregate '{aggregate.short_name}'",
        aggregate,
    )


@mcp.tool
def remove_aggregate(short_name: str) -> ResponsePayload[Aggregate]:
    """Remove an aggregate by exact short name."""
    manager = IndexedAggregateService.from_config(get_config())
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
    manager = IndexedAggregateService.from_config(get_config())
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
    manager = IndexedAggregateService.from_config(get_config())
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
    """List aggregates, optionally narrowed by deterministic filters.

    Omit filters to list all aggregates. Use filters when you know exact torrent
    hashes or SQLite GLOB patterns for aggregate short names, Bangumi original
    names, or Bangumi Chinese names. For natural-language discovery, use
    search_aggregates instead.
    """
    manager = IndexedAggregateService.from_config(get_config())
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
    SQLite GLOB patterns, use list_aggregates instead. Requires an initialized
    search index; call rebuild_search_index first if the index is empty, stale,
    or incomplete. The optional threshold is a minimum cosine similarity score.
    """
    manager = IndexedAggregateService.from_config(get_config())
    try:
        results = manager.search_aggregates(
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
def rebuild_search_index(
    force: bool = False,
) -> ResponsePayload[SearchIndexRebuildResult]:
    """Rebuild the semantic search index from the SQLite aggregate database.

    Use this when search_aggregates reports that the index is empty, stale, or
    incomplete. Set force to recompute every aggregate embedding even if its
    indexed source hash already matches the SQLite aggregate metadata.
    """
    manager = IndexedAggregateService.from_config(get_config())
    documents = manager.rebuild_search_index(force=force)
    return success(
        f"Rebuilt search index with {len(documents)} aggregates",
        SearchIndexRebuildResult(indexed_documents=len(documents), force=force),
    )


@mcp.tool
def check_health() -> ResponsePayload[HealthCheckReport]:
    """Run all Bonsai Manager health checks.

    The current checks verify that SQLite aggregates and LanceDB search documents
    are complete and consistent. Use rebuild_search_index to repair a failed
    search index consistency check.
    """
    report = IndexedAggregateService.check_health_from_config(get_config())
    message = "Health checks passed" if report.healthy else "Health checks failed"
    return success(message, report)


@mcp.tool
def audit_torrent_mapping(
    category: list[str] | None = None,
) -> ResponsePayload[TorrentMappingAudit]:
    """Audit qBittorrent torrents against stored aggregate mappings."""
    config = get_config()
    categories = category or list(config.audit_categories)
    manager = IndexedAggregateService.from_config(config)
    return success(
        "Audited torrent mapping",
        manager.audit_torrent_mapping(categories),
    )


def run_mcp_server(config: Config | None = None) -> None:
    global _config
    _config = config or load_config()
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    run_mcp_server()
