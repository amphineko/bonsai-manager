#!/usr/bin/env -S uv run python
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import click
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from rich.pretty import pprint

from config import PROJECT_ROOT
from lib.models import ResponsePayload
from lib.models.search import AggregateSearchResults
from scripts.sandbox import warn_if_sandboxed

if TYPE_CHECKING:
    from mcp.types import CallToolResult

MCP_TIMEOUT_SECONDS = 30.0


async def call_search_aggregates(query: str) -> CallToolResult:
    transport = StdioTransport(
        command="uv",
        args=["run", "python", "src/main.py", "mcp"],
        cwd=str(PROJECT_ROOT),
    )
    async with Client(transport) as client:
        return await client.call_tool_mcp(
            "search_aggregates",
            arguments={
                "query": query,
                "limit": 3,
            },
        )


@click.command("mcp-search-aggregates")
@click.argument("query")
def mcp_search_aggregates(query: str) -> None:
    """
    Search aggregates by natural-language text and return scored results.

    This command also serves as a smoke test for the MCP server.
    """
    warn_if_sandboxed("smoke test")

    try:
        tool_result = asyncio.run(
            asyncio.wait_for(call_search_aggregates(query), MCP_TIMEOUT_SECONDS)
        )
    except TimeoutError:
        raise click.ClickException(
            f"MCP search timed out after {MCP_TIMEOUT_SECONDS:.0f} seconds."
        ) from None
    if tool_result.isError:
        click.echo(tool_result.model_dump_json(indent=2))
        raise click.exceptions.Exit(1)

    payload = ResponsePayload.model_validate(tool_result.structuredContent)
    aggregates = AggregateSearchResults.model_validate(payload.data)
    for aggregate in aggregates.results:
        pprint(aggregate)


if __name__ == "__main__":
    mcp_search_aggregates()
