from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING

import click
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan

from config import Config
from lib.mcp import McpContext
from lib.models import ResponsePayload
from lib.models.aggregates import Aggregate
from lib.models.audit import AuditReport
from lib.models.health import HealthCheckReport
from lib.models.qbittorrent import QbittorrentTorrent
from lib.models.search import AggregateSearchResults, SearchIndexRebuildResult
from lib.models.sync import SyncReport
from lib.sync import create_sync_runner

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

type McpLifespanContext = dict[str, McpContext]


def success[DataT](message: str, data: DataT) -> ResponsePayload[DataT]:
    return ResponsePayload(message=message, data=data)


def create_mcp_server(config: Config) -> FastMCP[McpLifespanContext]:
    context = McpContext(config)

    def health_gated[ReturnT, **P](
        function: Callable[P, ReturnT],
    ) -> Callable[P, ReturnT]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> ReturnT:
            if not context.check_health_once().healthy:
                raise ToolError(
                    "Bonsai Manager health checks failed. Call check_health for "
                    "details, then call sync to repair derived state before retrying."
                )
            return function(*args, **kwargs)

        return wrapped

    @lifespan
    async def mcp_lifespan(
        _: FastMCP[McpLifespanContext],
    ) -> AsyncGenerator[McpLifespanContext]:
        try:
            yield {"mcp_context": context}
        finally:
            context.close()

    mcp = FastMCP("bonsai-manager", version="0.1.0", lifespan=mcp_lifespan)

    @mcp.resource("bonsai://aggregates/")
    def aggregate_summary() -> dict[str, int]:
        """Return a summary of the tracked aggregate collection."""
        return {"total": context.indexed.count_aggregates()}

    @mcp.tool
    @health_gated
    def add_aggregate(
        short_name: str,
        bangumi_subject_id: int | None = None,
        torrent_hashes: list[str] | None = None,
    ) -> ResponsePayload[Aggregate]:
        """Add a new aggregate, optionally seeded by Bangumi and torrent hashes."""
        aggregate = context.indexed.add_aggregate(
            short_name,
            bangumi_subject_id,
            torrent_hashes,
        )
        return success(
            f"Added aggregate '{aggregate.short_name}'",
            aggregate,
        )

    @mcp.tool
    @health_gated
    def remove_aggregate(short_name: str) -> ResponsePayload[Aggregate]:
        """Remove an aggregate by exact short name."""
        aggregate = context.indexed.remove_aggregate(short_name)
        return success(
            f"Removed aggregate '{aggregate.short_name}'",
            aggregate,
        )

    @mcp.tool
    @health_gated
    def update_aggregate_torrents(
        short_name: str,
        group: str | None = None,
        add_hashes: list[str] | None = None,
        remove_hashes: list[str] | None = None,
    ) -> ResponsePayload[dict[str, list[str]]]:
        """Add, move, and/or remove torrent hashes on an aggregate.

        Added hashes are placed in `group`, or in the derived `ungrouped` bucket
        when omitted. Adding a hash already on this aggregate moves it. Removed
        hashes are deleted regardless of their current group.
        """
        torrent_hashes = context.indexed.update_aggregate_torrents(
            short_name=short_name,
            group=group,
            add_hashes=add_hashes,
            remove_hashes=remove_hashes,
        )
        return success(
            f"Updated torrents for '{short_name}'",
            torrent_hashes,
        )

    @mcp.tool
    @health_gated
    def update_aggregate_bangumi_subjects(
        short_name: str,
        add_subject_ids: list[int] | None = None,
        remove_subject_ids: list[int] | None = None,
    ) -> ResponsePayload[list[int]]:
        """Add and/or remove Bangumi subject IDs on an existing aggregate."""
        subject_ids = context.indexed.update_aggregate_bangumi_subjects(
            short_name,
            add_subject_ids,
            remove_subject_ids,
        )
        return success(
            f"Updated Bangumi subjects for '{short_name}'",
            subject_ids,
        )

    @mcp.tool
    @health_gated
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
        aggregates = context.indexed.list_aggregates(
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
    def get_torrent_info(
        hashes: list[str],
    ) -> ResponsePayload[list[QbittorrentTorrent]]:
        """Resolve torrent hashes to live qBittorrent metadata in request order.

        Hashes absent from qBittorrent are omitted. Duplicate hashes are rejected.
        Use list_aggregates first to discover the hashes attached to an aggregate.
        """
        torrents = context.indexed.get_torrent_info(hashes)
        return success("Resolved torrent metadata", torrents)

    @mcp.tool
    @health_gated
    def search_aggregates(
        query: str,
        limit: int = 10,
        threshold: float | None = None,
    ) -> ResponsePayload[AggregateSearchResults]:
        """Search aggregates by natural-language text and return scored results.

        Use this for fuzzy semantic discovery, vague descriptions, translated title
        fragments, or cross-language title search. For exact torrent hashes or known
        SQLite GLOB patterns, use list_aggregates instead. Requires an initialized
        search index; call sync first if the index is empty, stale, or incomplete.
        The optional threshold is a minimum cosine similarity score.
        """
        try:
            results = context.indexed.search_aggregates(
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
    def sync(
        force: bool = False,
        audit_enabled: bool = True,
    ) -> ResponsePayload[SyncReport]:
        """Refresh configured sources, repair derived state, and run audits.

        This refreshes a configured Bangumi collection mirror when its TTL expires,
        reconciles LanceDB with SQLite, then optionally runs the configured read-only
        aggregate auditors. It never changes aggregate torrent membership. Set force
        to bypass source freshness checks and recompute all aggregate embeddings, or
        disable audit checks with audit_enabled.
        """
        health_before = context.check_health()
        report = create_sync_runner(
            context.indexed,
            force=force,
            audit_enabled=audit_enabled,
            health_before=health_before,
        ).run()
        context.set_health_report(report.health_after)
        message = (
            "Synchronization completed"
            if report.healthy
            else "Synchronization completed with errors"
        )
        return success(message, report)

    @mcp.tool
    def rebuild_search_index(
        force: bool = False,
    ) -> ResponsePayload[SearchIndexRebuildResult]:
        """Rebuild the semantic search index from the SQLite aggregate database.

        Use this when search_aggregates reports that the index is empty, stale, or
        incomplete. Set force to recompute every aggregate embedding even if its
        indexed source hash already matches the SQLite aggregate metadata.
        """
        documents = context.indexed.rebuild_search_index(force=force)
        context.check_health()
        return success(
            f"Rebuilt search index with {len(documents)} aggregates",
            SearchIndexRebuildResult(
                indexed_documents=len(documents),
                force=force,
            ),
        )

    @mcp.tool
    def check_health() -> ResponsePayload[HealthCheckReport]:
        """Run all Bonsai Manager health checks.

        The current checks verify that SQLite aggregates and LanceDB search documents
        are complete and consistent. Use sync to repair a failed search index
        consistency check and run configured aggregate auditors.
        """
        report = context.check_health()
        message = "Health checks passed" if report.healthy else "Health checks failed"
        return success(message, report)

    @mcp.tool
    @health_gated
    def audit(
        category: list[str] | None = None,
    ) -> ResponsePayload[AuditReport]:
        """Run configured read-only aggregate audit checks."""
        categories = category or list(context.config.audit_categories)
        report = context.indexed.run_audit(categories)
        return ResponsePayload(
            status="success" if report.successful else "error",
            message=(
                "Aggregate audits completed"
                if report.successful
                else "Aggregate audits failed"
            ),
            data=report,
        )

    return mcp


@click.command(name="mcp")
@click.pass_obj
def run_mcp_server(config: Config) -> None:
    """Run the Bonsai Manager MCP server over stdio."""
    create_mcp_server(config).run(transport="stdio", show_banner=False)
