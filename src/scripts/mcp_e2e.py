#!/usr/bin/env -S uv run python
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Self, override
from urllib.parse import parse_qs, urlsplit

import click
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pydantic import TypeAdapter

from config import PROJECT_ROOT
from lib.models import ResponsePayload
from lib.models.aggregates import Aggregate, Torrent
from lib.models.bangumi import BangumiSubject, BangumiSubjectSnapshot, BangumiTag
from scripts.sandbox import warn_if_sandboxed

MCP_TIMEOUT_SECONDS = 30.0

SUBJECT_ID = 123456
SHORT_NAME = "MCP E2E Fixture"
INITIAL_HASHES = [
    "1111111111111111111111111111111111111111",
    "2222222222222222222222222222222222222222",
]
ADDED_HASH = "3333333333333333333333333333333333333333"

TORRENTS = {
    INITIAL_HASHES[0]: "Fixture Episode 01",
    INITIAL_HASHES[1]: "Fixture Episode 02",
    ADDED_HASH: "Fixture Episode 03",
}

logger = logging.getLogger(__name__)


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        logger.info("mock request: POST %s", self.path)
        match self.path:
            case "/api/v2/auth/login":
                logger.info("mock qBittorrent login")
                self.send_json({"status": "ok"})
            case _:
                logger.warning("mock unhandled POST: %s", self.path)
                self.send_error(404)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        logger.info("mock request: GET %s", self.path)
        match parsed.path:
            case subject_path if subject_path == f"/v0/subjects/{SUBJECT_ID}":
                logger.info("mock Bangumi subject lookup: %s", SUBJECT_ID)
                self.send_json(
                    {
                        "name": "MCP E2E Subject",
                        "name_cn": "MCP E2E 中文名",
                        "type": 2,
                        "tags": [{"name": "e2e", "count": 1}],
                    }
                )
            case "/api/v2/torrents/info":
                params = parse_qs(parsed.query)
                hashes = params.get("hashes", [])
                torrent_hashes = hashes[0].split("|") if hashes else list(TORRENTS)
                logger.info("mock qBittorrent torrent info: %s", torrent_hashes)
                self.send_json(
                    [
                        {
                            "hash": torrent_hash,
                            "name": TORRENTS[torrent_hash],
                            "category": "anime",
                            "save_path": "/downloads",
                        }
                        for torrent_hash in torrent_hashes
                        if torrent_hash in TORRENTS
                    ]
                )
            case "/api/v2/torrents/files":
                params = parse_qs(parsed.query)
                torrent_hash = params.get("hash", [""])[0]
                logger.info("mock qBittorrent torrent files: %s", torrent_hash)
                self.send_json(
                    [
                        {
                            "name": f"{TORRENTS.get(torrent_hash, 'unknown')}.mkv",
                            "size": 1,
                            "progress": 1.0,
                            "priority": 1,
                            "is_seed": False,
                        }
                    ]
                )
            case _:
                logger.warning("mock unhandled GET: %s", self.path)
                self.send_error(404)

    def send_json(self, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @override
    def log_message(self, format: str, *args: object) -> None:
        return


class MockHttpServer:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        return "http://127.0.0.1"

    @property
    def port(self) -> int:
        return int(self.server.server_port)

    @property
    def base_url(self) -> str:
        return f"{self.host}:{self.port}"

    def __enter__(self) -> Self:
        logger.info("starting mock HTTP server: %s", self.base_url)
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        logger.info("stopping mock HTTP server")
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


async def call_tool(
    client: Client[Any],
    name: str,
    arguments: dict[str, object],
) -> object:
    logger.info("calling MCP tool: %s %s", name, arguments)
    result = await client.call_tool_mcp(name, arguments=arguments)
    if result.isError:
        logger.error("MCP tool failed: %s", name)
        raise AssertionError(result.model_dump_json(indent=2))
    logger.info("MCP tool succeeded: %s", name)
    return result.structuredContent


class McpE2ETest(unittest.IsolatedAsyncioTestCase):
    temp_dir: tempfile.TemporaryDirectory[str]
    db_path: Path
    mock_server: MockHttpServer

    @override
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="bonsai-mcp-e2e-")
        self.db_path = Path(self.temp_dir.name) / "db.sqlite3"
        self.mock_server = MockHttpServer()
        self.mock_server.__enter__()
        logger.info("prepared temporary SQLite DB: %s", self.db_path)

    @override
    async def asyncTearDown(self) -> None:
        await asyncio.to_thread(self.mock_server.__exit__, None, None, None)
        self.temp_dir.cleanup()
        logger.info("cleaned up temporary SQLite DB")

    async def test_aggregate_torrent_flow(self) -> None:
        try:
            await asyncio.wait_for(self.run_mcp_flow(), MCP_TIMEOUT_SECONDS)
        except TimeoutError:
            self.fail(f"MCP E2E timed out after {MCP_TIMEOUT_SECONDS:.0f} seconds.")

    async def run_mcp_flow(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "DB_PATH": str(self.db_path),
                "QBIT_HOST": self.mock_server.host,
                "QBIT_PORT": str(self.mock_server.port),
                "QBIT_USERNAME": "mock",
                "QBIT_PASSWORD": "mock",
                "BANGUMI_BASE_URL": self.mock_server.base_url,
                "BANGUMI_TOKEN": "",
                "SEARCH_LANCEDB_PATH": str(
                    Path(self.temp_dir.name) / "aggregate_search.lancedb"
                ),
            }
        )
        transport = StdioTransport(
            command="uv",
            args=["run", "python", "src/main.py", "mcp"],
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        async with Client(transport) as client:
            logger.info("test step: add aggregate with subject and initial torrents")
            added = await call_tool(
                client,
                "add_aggregate",
                {
                    "short_name": SHORT_NAME,
                    "bangumi_subject_id": SUBJECT_ID,
                    "torrent_hashes": INITIAL_HASHES,
                },
            )
            added_aggregate = ResponsePayload[Aggregate].model_validate(added).data
            self.assertEqual(
                added_aggregate,
                expected_aggregate(
                    INITIAL_HASHES,
                    added_aggregate.bangumi_subjects[0].last_updated_at,
                ),
            )

            logger.info("test step: read aggregate collection summary resource")
            resources = await client.list_resources()
            self.assertIn(
                "bonsai://aggregates/", {str(resource.uri) for resource in resources}
            )
            summary_contents = await client.read_resource("bonsai://aggregates/")
            self.assertEqual(json.loads(summary_contents[0].text), {"total": 1})

            logger.info("test step: list aggregate after add")
            listed = await call_tool(
                client,
                "list_aggregates",
                {"filter_short_name": [SHORT_NAME]},
            )
            listed_aggregates = (
                ResponsePayload[list[Aggregate]].model_validate(listed).data
            )
            self.assertEqual(
                listed_aggregates,
                [
                    expected_aggregate(
                        INITIAL_HASHES,
                        listed_aggregates[0].bangumi_subjects[0].last_updated_at,
                    )
                ],
            )

            logger.info("test step: list all aggregates without filters")
            listed_all = await call_tool(client, "list_aggregates", {})
            all_aggregates = (
                ResponsePayload[list[Aggregate]].model_validate(listed_all).data
            )
            self.assertEqual(
                all_aggregates,
                [
                    expected_aggregate(
                        INITIAL_HASHES,
                        all_aggregates[0].bangumi_subjects[0].last_updated_at,
                    )
                ],
            )

            logger.info("test step: list aggregate by torrent hash")
            listed_by_hash = await call_tool(
                client,
                "list_aggregates",
                {"filter_torrent_hashes": [INITIAL_HASHES[0]]},
            )
            hash_matches = (
                ResponsePayload[list[Aggregate]].model_validate(listed_by_hash).data
            )
            self.assertEqual(
                hash_matches,
                [
                    expected_aggregate(
                        INITIAL_HASHES,
                        hash_matches[0].bangumi_subjects[0].last_updated_at,
                    )
                ],
            )

            logger.info("test step: list aggregate by Bangumi GLOB filters")
            listed_by_subject = await call_tool(
                client,
                "list_aggregates",
                {
                    "filter_bangumi_subject_name": ["*Subject"],
                    "filter_bangumi_subject_cn_name": ["*中文名"],
                },
            )
            subject_matches = (
                ResponsePayload[list[Aggregate]].model_validate(listed_by_subject).data
            )
            self.assertEqual(
                subject_matches,
                [
                    expected_aggregate(
                        INITIAL_HASHES,
                        subject_matches[0].bangumi_subjects[0].last_updated_at,
                    )
                ],
            )

            logger.info("test step: add a new torrent")
            after_add = await call_tool(
                client,
                "update_aggregate_torrents",
                {"short_name": SHORT_NAME, "add_hashes": [ADDED_HASH]},
            )
            hashes_after_add = (
                TypeAdapter(ResponsePayload[list[str]]).validate_python(after_add).data
            )
            self.assertEqual(hashes_after_add, [*INITIAL_HASHES, ADDED_HASH])
            listed_after_add = await self.list_test_aggregate(client)
            self.assertEqual(
                listed_after_add,
                expected_aggregate(
                    [*INITIAL_HASHES, ADDED_HASH],
                    listed_after_add.bangumi_subjects[0].last_updated_at,
                ),
            )

            logger.info("test step: remove an existing torrent")
            after_remove = await call_tool(
                client,
                "update_aggregate_torrents",
                {"short_name": SHORT_NAME, "remove_hashes": [INITIAL_HASHES[0]]},
            )
            hashes_after_remove = (
                TypeAdapter(ResponsePayload[list[str]])
                .validate_python(after_remove)
                .data
            )
            self.assertEqual(hashes_after_remove, [INITIAL_HASHES[1], ADDED_HASH])
            listed_after_remove = await self.list_test_aggregate(client)
            self.assertEqual(
                listed_after_remove,
                expected_aggregate(
                    [INITIAL_HASHES[1], ADDED_HASH],
                    listed_after_remove.bangumi_subjects[0].last_updated_at,
                ),
            )

    async def list_test_aggregate(self, client: Client[Any]) -> Aggregate:
        listed = await call_tool(
            client,
            "list_aggregates",
            {"filter_short_name": [SHORT_NAME]},
        )
        listed_aggregates = ResponsePayload[list[Aggregate]].model_validate(listed).data
        self.assertEqual(len(listed_aggregates), 1)
        return listed_aggregates[0]


def expected_aggregate(torrent_hashes: list[str], last_updated_at: str) -> Aggregate:
    return Aggregate(
        short_name=SHORT_NAME,
        category="anime",
        bangumi_subjects=[
            BangumiSubject(
                subject_id=SUBJECT_ID,
                last_updated_at=last_updated_at,
                snapshot=BangumiSubjectSnapshot(
                    name="MCP E2E Subject",
                    name_cn="MCP E2E 中文名",
                    type=2,
                    tags=[BangumiTag(name="e2e", count=1)],
                ),
            )
        ],
        torrents=[Torrent(hash=torrent_hash) for torrent_hash in torrent_hashes],
    )


@click.command("mcp-e2e")
def mcp_e2e() -> None:
    """Run an E2E smoke test against the MCP server with mock HTTP services."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    warn_if_sandboxed("E2E test")

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(McpE2ETest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise click.exceptions.Exit(1)

    click.echo("MCP E2E passed.")


if __name__ == "__main__":
    mcp_e2e()
