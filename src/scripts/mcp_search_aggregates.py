#!/usr/bin/env -S uv run python
from __future__ import annotations

import asyncio
import socket

import click
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp.types import CallToolResult
from rich.pretty import pprint

from config import PROJECT_ROOT
from lib.models import ResponsePayload
from lib.models.search import AggregateSearchResults

MCP_TIMEOUT_SECONDS = 30.0

SANDBOX_WARNING = (
    "Warning: this environment blocks socketpair writes. asyncio/AnyIO MCP stdio "
    "clients may hang here; run this smoke test outside the sandbox if it times out."
)


def socketpair_write_is_blocked() -> bool:
    try:
        read_socket, write_socket = socket.socketpair()
    except OSError:
        return False
    try:
        try:
            write_socket.send(b"x")
        except PermissionError:
            return True
        return False
    finally:
        read_socket.close()
        write_socket.close()


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
    if socketpair_write_is_blocked():
        click.echo(SANDBOX_WARNING, err=True)

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
