#!/usr/bin/env -S uv run python
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self, override
from urllib.parse import parse_qs, urlsplit

import click
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp.types import TextResourceContents
from pydantic import TypeAdapter, ValidationError

from config import PROJECT_ROOT, load_config
from lib.models import ResponsePayload
from lib.models.aggregates import Aggregate, Torrent
from lib.models.audit import (
    AuditCheckResult,
    AuditCheckStatus,
    AuditFinding,
    AuditReport,
    AuditSeverity,
)
from lib.models.bangumi import BangumiSubject, BangumiSubjectSnapshot, BangumiTag
from lib.models.health import HealthCheckReport, SearchIndexConsistencyCheck
from lib.models.qbittorrent import QbittorrentTorrent
from lib.models.search import SearchIndexRebuildResult
from lib.models.sync import (
    AuditSyncResult,
    BangumiCollectionSyncResult,
    SearchIndexSyncResult,
    SyncReport,
    SyncStepStatus,
)
from lib.search.repositories import LanceDbSearchRepository
from scripts.sandbox import warn_if_sandboxed

if TYPE_CHECKING:
    from collections.abc import Iterable

MCP_TIMEOUT_SECONDS = 45.0

SUBJECT_ID = 123456
SHORT_NAME = "MCP E2E Fixture"
INITIAL_HASHES = [
    "1111111111111111111111111111111111111111",
    "2222222222222222222222222222222222222222",
]
ADDED_HASH = "3333333333333333333333333333333333333333"
EMPTY_SHORT_NAME = "Empty Aggregate"
MIXED_SHORT_NAME = "Mixed Groups"
GROUPED_SHORT_NAME = "Grouped Torrents"
MIXED_HASHES = [str(number) * 40 for number in range(4, 8)]
GROUPED_HASHES = [str(number) * 40 for number in range(8, 10)]
DIRECT_GROUP_HASH = "a" * 40
DIRECT_UNGROUPED_HASH = "b" * 40

TORRENTS = {
    INITIAL_HASHES[0]: "Fixture Episode 01",
    INITIAL_HASHES[1]: "Fixture Episode 02",
    ADDED_HASH: "Fixture Episode 03",
    **{
        torrent_hash: f"Grouped Fixture {index:02d}"
        for index, torrent_hash in enumerate(
            [*MIXED_HASHES, *GROUPED_HASHES, DIRECT_GROUP_HASH, DIRECT_UNGROUPED_HASH],
            start=4,
        )
    },
}

logger = logging.getLogger(__name__)


class MockHandler(BaseHTTPRequestHandler):
    torrent_info_requests: ClassVar[list[list[str]]] = []
    collection_requests: ClassVar[list[dict[str, list[str]]]] = []
    fail_torrent_info_requests: ClassVar[bool] = False

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
            case "/v0/users/mcp-fixture/collections":
                params = parse_qs(parsed.query)
                self.collection_requests.append(params)
                logger.info("mock Bangumi collection lookup: %s", params)
                self.send_json(
                    {
                        "total": 1,
                        "limit": int(params.get("limit", ["50"])[0]),
                        "offset": int(params.get("offset", ["0"])[0]),
                        "data": [
                            {
                                "subject_id": SUBJECT_ID,
                                "subject_type": 2,
                                "type": 3,
                                "updated_at": "2026-08-03T12:00:00+00:00",
                                "subject": {
                                    "name": "MCP E2E Subject",
                                    "name_cn": "MCP E2E 中文名",
                                    "type": 2,
                                    "tags": [{"name": "e2e", "count": 1}],
                                },
                            }
                        ],
                    }
                )
            case "/api/v2/torrents/info":
                if self.fail_torrent_info_requests:
                    logger.info("mock qBittorrent torrent info failure")
                    self.send_error(503)
                    return
                params = parse_qs(parsed.query)
                hashes = params.get("hashes", [])
                torrent_hashes = hashes[0].split("|") if hashes else list(TORRENTS)
                self.torrent_info_requests.append(torrent_hashes)
                logger.info("mock qBittorrent torrent info: %s", torrent_hashes)
                self.send_json(
                    [
                        {
                            "hash": torrent_hash,
                            "name": TORRENTS[torrent_hash],
                            "category": "anime",
                            "save_path": "/downloads",
                        }
                        for torrent_hash in reversed(torrent_hashes)
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
        MockHandler.torrent_info_requests.clear()
        MockHandler.collection_requests.clear()
        MockHandler.fail_torrent_info_requests = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        return "http://127.0.0.1"

    @property
    def port(self) -> int:
        return self.server.server_port

    @property
    def base_url(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def torrent_info_requests(self) -> list[list[str]]:
        return list(MockHandler.torrent_info_requests)

    @property
    def collection_requests(self) -> list[dict[str, list[str]]]:
        return list(MockHandler.collection_requests)

    def set_torrent_info_failure(self, enabled: bool) -> None:
        MockHandler.fail_torrent_info_requests = enabled

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
    search_path: Path
    mock_server: MockHttpServer

    @override
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="bonsai-mcp-e2e-")
        self.db_path = Path(self.temp_dir.name) / "db.sqlite3"
        self.search_path = Path(self.temp_dir.name) / "aggregate_search.lancedb"
        self.mock_server = MockHttpServer()
        self.mock_server.__enter__()
        logger.info("prepared temporary SQLite DB: %s", self.db_path)

    @override
    async def asyncTearDown(self) -> None:
        await asyncio.to_thread(self.mock_server.__exit__, None, None, None)
        self.temp_dir.cleanup()
        logger.info("cleaned up temporary SQLite DB")

    async def test_health_gate_initialization(self) -> None:
        async with asyncio.timeout(MCP_TIMEOUT_SECONDS):
            env = self.mcp_env()
            async with Client(self.mcp_transport(env)) as client:
                await self.assert_tool_schemas(client)
                await self.initialize_stores_and_open_gate(client)

    async def test_aggregate_torrent_flow(self) -> None:
        async with asyncio.timeout(MCP_TIMEOUT_SECONDS):
            env = self.mcp_env()
            async with Client(self.mcp_transport(env)) as client:
                await self.initialize_healthy_stores(client)
                await self.add_test_aggregate(client)
                self.assert_integer_aggregate_schema()
                await self.assert_summary_resource(client)
                await self.assert_listing_queries(client)
                await self.assert_torrent_info_lookup(client)
                await self.assert_torrent_updates(client)

    async def test_torrent_group_flow(self) -> None:
        async with asyncio.timeout(MCP_TIMEOUT_SECONDS):
            env = self.mcp_env()
            async with Client(self.mcp_transport(env)) as client:
                await self.initialize_healthy_stores(client)
                await self.assert_torrent_group_states(client)
                await self.assert_torrent_group_updates(client)
                await self.assert_torrent_group_validation(client)
                await self.assert_torrent_group_cascade(client)

    async def test_health_gate_drift_recovery(self) -> None:
        async with asyncio.timeout(MCP_TIMEOUT_SECONDS):
            env = self.mcp_env()
            async with Client(self.mcp_transport(env)) as client:
                await self.initialize_healthy_stores(client)
                await self.add_test_aggregate(client)
                await self.assert_health_gate_and_recovery(client, env)

    async def test_bangumi_collection_sync_ttl(self) -> None:
        async with asyncio.timeout(MCP_TIMEOUT_SECONDS):
            env = self.mcp_env()
            env["BANGUMI_USERNAME"] = "mcp-fixture"
            env["BANGUMI_COLLECTION_TTL_SECONDS"] = "21600"
            async with Client(self.mcp_transport(env)) as client:
                first = (
                    ResponsePayload[SyncReport]
                    .model_validate(await call_tool(client, "sync", {}))
                    .data
                )
                first_collection = first.steps[0]
                self.assertIsInstance(
                    first_collection,
                    BangumiCollectionSyncResult,
                )
                self.assertEqual(first_collection.status, SyncStepStatus.COMPLETED)
                self.assertEqual(
                    (first_collection.fetched, first_collection.created),
                    (1, 1),
                )

                second = (
                    ResponsePayload[SyncReport]
                    .model_validate(await call_tool(client, "sync", {}))
                    .data
                )
                second_collection = second.steps[0]
                self.assertIsInstance(
                    second_collection,
                    BangumiCollectionSyncResult,
                )
                self.assertEqual(second_collection.status, SyncStepStatus.SKIPPED)
                self.assertEqual(len(self.mock_server.collection_requests), 1)

                forced = (
                    ResponsePayload[SyncReport]
                    .model_validate(await call_tool(client, "sync", {"force": True}))
                    .data
                )
                forced_collection = forced.steps[0]
                self.assertIsInstance(
                    forced_collection,
                    BangumiCollectionSyncResult,
                )
                self.assertEqual(forced_collection.status, SyncStepStatus.COMPLETED)
                self.assertEqual(forced_collection.unchanged, 1)
                self.assertEqual(len(self.mock_server.collection_requests), 2)

            with sqlite3.connect(self.db_path) as connection:
                collection_row = connection.execute(
                    """
                    SELECT username, subject_id, collection_type, removed_at
                    FROM bangumi_user_collections
                    """
                ).fetchone()
                sync_state_count = connection.execute(
                    "SELECT count(*) FROM bangumi_collection_sync_states"
                ).fetchone()
            self.assertEqual(collection_row, ("mcp-fixture", SUBJECT_ID, 3, None))
            self.assertEqual(sync_state_count, (1,))

    def mcp_transport(self, env: dict[str, str]) -> StdioTransport:
        return StdioTransport(
            command="uv",
            args=["run", "python", "src/main.py", "mcp"],
            cwd=str(PROJECT_ROOT),
            env=env,
        )

    def mcp_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "DB_PATH": str(self.db_path),
                "QBIT_HOST": self.mock_server.host,
                "QBIT_PORT": str(self.mock_server.port),
                "QBIT_USERNAME": "mock",
                "QBIT_PASSWORD": "mock",  # pragma: allowlist secret
                "BANGUMI_BASE_URL": self.mock_server.base_url,
                "BANGUMI_TOKEN": "",
                "SEARCH_LANCEDB_PATH": str(self.search_path),
            }
        )
        return env

    def assert_integer_aggregate_schema(self) -> None:
        logger.info("test step: aggregate relationships use integer IDs")
        with sqlite3.connect(self.db_path) as connection:
            aggregate_id = connection.execute(
                "SELECT typeof(id), id FROM aggregates WHERE short_name = ?",
                (SHORT_NAME,),
            ).fetchone()
            reference_types = connection.execute(
                """
                SELECT
                    (SELECT typeof(aggregate_id) FROM torrents LIMIT 1),
                    (SELECT typeof(aggregate_id)
                     FROM aggregate_bangumi_subjects LIMIT 1)
                """
            ).fetchone()
            declared_types = {
                table: next(
                    row[2]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                    if row[1] == "aggregate_id"
                )
                for table in ("torrents", "aggregate_bangumi_subjects")
            }
            foreign_key_errors = list(connection.execute("PRAGMA foreign_key_check"))

        self.assertEqual(aggregate_id, ("integer", 1))
        self.assertEqual(reference_types, ("integer", "integer"))
        self.assertEqual(
            declared_types,
            {"torrents": "INTEGER", "aggregate_bangumi_subjects": "INTEGER"},
        )
        self.assertEqual(foreign_key_errors, [])

    async def assert_tool_schemas(self, client: Client[Any]) -> None:
        logger.info("test step: MCP context parameters are hidden from tool schemas")
        for tool in await client.list_tools():
            properties = tool.inputSchema.get("properties", {})
            self.assertNotIn("context", properties)
            self.assertNotIn("_fastmcp_context", properties)

    async def initialize_stores_and_open_gate(self, client: Client[Any]) -> None:
        logger.info(
            "test step: health check reports missing stores without creating them"
        )
        initial_health_report = await self.call_health_check(client)
        self.assertFalse(initial_health_report.healthy)
        self.assertFalse(initial_health_report.checks[0].sqlite_ready)
        self.assertFalse(initial_health_report.checks[0].lancedb_ready)
        self.assertFalse(self.db_path.exists())
        self.assertFalse(self.search_path.exists())

        logger.info("test step: health check rejects uninitialized stores")
        self.db_path.touch()
        self.search_path.mkdir()
        uninitialized_health_report = await self.call_health_check(client)
        self.assertFalse(uninitialized_health_report.healthy)
        self.assertFalse(uninitialized_health_report.checks[0].sqlite_ready)
        self.assertFalse(uninitialized_health_report.checks[0].lancedb_ready)
        self.assertEqual(self.db_path.stat().st_size, 0)
        self.assertEqual(list(self.search_path.iterdir()), [])

        logger.info("test step: unhealthy services gate aggregate tools")
        blocked_add = await client.call_tool_mcp(
            "add_aggregate",
            arguments={"short_name": SHORT_NAME},
        )
        self.assertTrue(blocked_add.isError)
        self.assertIn("health checks failed", str(blocked_add.content))

        logger.info("test step: sync initializes stores and opens the gate")
        initialized = await call_tool(client, "sync", {})
        initialized_report = (
            ResponsePayload[SyncReport].model_validate(initialized).data
        )
        expected_health_after = HealthCheckReport(
            healthy=True,
            checks=[
                SearchIndexConsistencyCheck(
                    healthy=True,
                    aggregate_count=0,
                    document_count=0,
                    missing_documents=[],
                    orphaned_documents=[],
                    stale_documents=[],
                    duplicate_documents=[],
                )
            ],
        )
        self.assertEqual(
            initialized_report,
            SyncReport(
                healthy=True,
                health_before=uninitialized_health_report,
                health_after=expected_health_after,
                steps=[
                    BangumiCollectionSyncResult(
                        status=SyncStepStatus.SKIPPED,
                        reason="BANGUMI_USERNAME is not configured.",
                    ),
                    SearchIndexSyncResult(
                        status=SyncStepStatus.COMPLETED,
                        indexed_documents=0,
                        force=False,
                    ),
                    AuditSyncResult(
                        status=SyncStepStatus.COMPLETED,
                        report=expected_audit_report([]),
                    ),
                ],
            ),
        )
        self.assertTrue((await self.call_health_check(client)).healthy)

    async def initialize_healthy_stores(self, client: Client[Any]) -> None:
        logger.info("test step: rebuild initializes empty stores and opens the gate")
        initialized = await call_tool(client, "rebuild_search_index", {})
        initialized_result = (
            TypeAdapter(ResponsePayload[SearchIndexRebuildResult])
            .validate_python(initialized)
            .data
        )
        self.assertEqual(
            initialized_result,
            SearchIndexRebuildResult(indexed_documents=0, force=False),
        )
        self.assertTrue((await self.call_health_check(client)).healthy)

    async def add_test_aggregate(self, client: Client[Any]) -> None:
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

    async def assert_summary_resource(self, client: Client[Any]) -> None:
        logger.info("test step: read aggregate collection summary resource")
        resources = await client.list_resources()
        self.assertIn(
            "bonsai://aggregates/", {str(resource.uri) for resource in resources}
        )
        summary_contents = await client.read_resource("bonsai://aggregates/")
        summary_content = summary_contents[0]
        if not isinstance(summary_content, TextResourceContents):
            self.fail("Aggregate summary resource did not return text content.")
        self.assertEqual(json.loads(summary_content.text), {"total": 1})

    async def assert_health_gate_and_recovery(
        self,
        client: Client[Any],
        env: dict[str, str],
    ) -> None:
        logger.info("test step: health check passes for synchronized stores")
        healthy_report = await self.call_health_check(client)
        self.assertEqual(
            healthy_report,
            HealthCheckReport(
                healthy=True,
                checks=[
                    SearchIndexConsistencyCheck(
                        healthy=True,
                        aggregate_count=1,
                        document_count=1,
                        missing_documents=[],
                        orphaned_documents=[],
                        stale_documents=[],
                        duplicate_documents=[],
                    )
                ],
            ),
        )

        logger.info("test step: remove search document to simulate index drift")
        search_config = replace(
            load_config().search,
            lancedb_path=self.search_path,
        )
        await asyncio.to_thread(
            LanceDbSearchRepository(search_config).delete_document,
            SHORT_NAME,
        )
        unhealthy_report = await self.call_health_check(client)
        self.assertFalse(unhealthy_report.healthy)
        self.assertEqual(
            unhealthy_report.checks[0].missing_documents,
            [SHORT_NAME],
        )

        logger.info("test step: detected index drift closes the tool gate")
        blocked_list = await client.call_tool_mcp(
            "list_aggregates",
            arguments={},
        )
        self.assertTrue(blocked_list.isError)
        self.assertIn("health checks failed", str(blocked_list.content))

        logger.info("test step: unhealthy CLI health check exits with failure")
        unhealthy_cli_result = await asyncio.to_thread(run_health_cli, env)
        self.assertEqual(
            unhealthy_cli_result.returncode,
            1,
            unhealthy_cli_result.stdout,
        )
        self.assertIn("HealthCheckReport(", unhealthy_cli_result.stdout)

        logger.info("test step: sync repairs search index and audits qBittorrent")
        synced = await call_tool(client, "sync", {})
        synced_report = ResponsePayload[SyncReport].model_validate(synced).data
        expected_healthy_report = HealthCheckReport(
            healthy=True,
            checks=[
                SearchIndexConsistencyCheck(
                    healthy=True,
                    aggregate_count=1,
                    document_count=1,
                    missing_documents=[],
                    orphaned_documents=[],
                    stale_documents=[],
                    duplicate_documents=[],
                )
            ],
        )
        self.assertEqual(
            synced_report,
            SyncReport(
                healthy=True,
                health_before=unhealthy_report,
                health_after=expected_healthy_report,
                steps=[
                    BangumiCollectionSyncResult(
                        status=SyncStepStatus.SKIPPED,
                        reason="BANGUMI_USERNAME is not configured.",
                    ),
                    SearchIndexSyncResult(
                        status=SyncStepStatus.COMPLETED,
                        indexed_documents=1,
                        force=False,
                    ),
                    AuditSyncResult(
                        status=SyncStepStatus.COMPLETED,
                        report=expected_audit_report(),
                    ),
                ],
            ),
        )
        self.assertTrue((await self.call_health_check(client)).healthy)

        logger.info("test step: generic MCP audit returns the configured checks")
        audited = await call_tool(client, "audit", {})
        self.assertEqual(
            ResponsePayload[AuditReport].model_validate(audited).data,
            expected_audit_report(),
        )

        logger.info("test step: CLI audit uses the same configured runner")
        audit_cli_result = await asyncio.to_thread(run_audit_cli, env)
        self.assertEqual(
            audit_cli_result.returncode,
            0,
            audit_cli_result.stdout,
        )
        self.assertIn("AuditReport(", audit_cli_result.stdout)
        self.assertIn("torrent.tracked_found", audit_cli_result.stdout)

        logger.info("test step: failed MCP audit returns an error payload with report")
        self.mock_server.set_torrent_info_failure(True)
        failed_audit_raw = await call_tool(client, "audit", {})
        self.mock_server.set_torrent_info_failure(False)
        failed_audit = ResponsePayload[AuditReport].model_validate(failed_audit_raw)
        self.assertEqual(failed_audit.status, "error")
        self.assertFalse(failed_audit.data.successful)
        self.assertEqual(
            failed_audit.data.checks[0].status,
            AuditCheckStatus.FAILED,
        )
        self.assertIn("HTTPError", failed_audit.data.checks[0].error or "")

        logger.info("test step: run CLI sync through the same orchestration service")
        sync_cli_result = await asyncio.to_thread(run_sync_cli, env)
        self.assertEqual(sync_cli_result.returncode, 0, sync_cli_result.stdout)
        self.assertIn("SyncReport(", sync_cli_result.stdout)
        self.assertIn("healthy=True", sync_cli_result.stdout)

        logger.info("test step: run CLI health check")
        cli_result = await asyncio.to_thread(run_health_cli, env)
        self.assertEqual(cli_result.returncode, 0, cli_result.stdout)
        self.assertIn("HealthCheckReport(", cli_result.stdout)
        self.assertIn("name='search_index_consistency'", cli_result.stdout)

    async def assert_listing_queries(self, client: Client[Any]) -> None:
        logger.info("test step: list aggregate after add")
        listed = await call_tool(
            client,
            "list_aggregates",
            {"filter_short_name": [SHORT_NAME]},
        )
        listed_aggregates = ResponsePayload[list[Aggregate]].model_validate(listed).data
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

    async def assert_torrent_updates(self, client: Client[Any]) -> None:
        logger.info("test step: add a new torrent")
        after_add = await call_tool(
            client,
            "update_aggregate_torrents",
            {"short_name": SHORT_NAME, "add_hashes": [ADDED_HASH]},
        )
        hashes_after_add = (
            ResponsePayload[dict[str, list[str]]].model_validate(after_add).data
        )
        self.assertEqual(
            hashes_after_add,
            {"ungrouped": [*INITIAL_HASHES, ADDED_HASH]},
        )
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
            ResponsePayload[dict[str, list[str]]].model_validate(after_remove).data
        )
        self.assertEqual(
            hashes_after_remove,
            {"ungrouped": [INITIAL_HASHES[1], ADDED_HASH]},
        )
        listed_after_remove = await self.list_test_aggregate(client)
        self.assertEqual(
            listed_after_remove,
            expected_aggregate(
                [INITIAL_HASHES[1], ADDED_HASH],
                listed_after_remove.bangumi_subjects[0].last_updated_at,
            ),
        )

    async def assert_torrent_info_lookup(self, client: Client[Any]) -> None:
        logger.info("test step: resolve torrent hashes to live qBittorrent metadata")
        missing_hash = "f" * 40
        requested_hashes = [INITIAL_HASHES[1], missing_hash, INITIAL_HASHES[0]]
        resolved = await call_tool(
            client,
            "get_torrent_info",
            {"hashes": requested_hashes},
        )
        torrents = (
            ResponsePayload[list[QbittorrentTorrent]].model_validate(resolved).data
        )
        self.assertEqual(
            torrents,
            [
                expected_qbittorrent_torrent(INITIAL_HASHES[1]),
                expected_qbittorrent_torrent(INITIAL_HASHES[0]),
            ],
        )
        self.assertEqual(
            self.mock_server.torrent_info_requests[-1],
            requested_hashes,
        )

        request_count = len(self.mock_server.torrent_info_requests)
        empty = await call_tool(client, "get_torrent_info", {"hashes": []})
        self.assertEqual(
            ResponsePayload[list[QbittorrentTorrent]].model_validate(empty).data,
            [],
        )
        self.assertEqual(len(self.mock_server.torrent_info_requests), request_count)

        await self.assert_tool_error(
            client,
            "get_torrent_info",
            {"hashes": [DIRECT_GROUP_HASH, DIRECT_GROUP_HASH.upper()]},
            "duplicates",
        )
        self.assertEqual(len(self.mock_server.torrent_info_requests), request_count)

    async def assert_torrent_group_states(self, client: Client[Any]) -> None:
        logger.info("test step: create empty, ungrouped, mixed, and grouped aggregates")
        normalized = Aggregate(
            short_name="Normalized Groups",
            torrents={
                "Z": torrent_models(reversed(INITIAL_HASHES)),
                "empty": [],
                " Group A ": torrent_models([ADDED_HASH]),
                "ungrouped": torrent_models([DIRECT_GROUP_HASH]),
            },
        )
        self.assertEqual(
            list(normalized.torrents),
            ["ungrouped", "Group A", "Z"],
        )
        self.assertEqual(
            normalized.torrent_hashes_by_group()["Z"],
            INITIAL_HASHES,
        )
        self.assertEqual(
            Aggregate.model_validate(
                {
                    "short_name": "Legacy Flat Torrents",
                    "torrents": [{"hash": INITIAL_HASHES[0]}],
                }
            ),
            simple_aggregate(
                "Legacy Flat Torrents",
                {"ungrouped": torrent_models([INITIAL_HASHES[0]])},
            ),
        )
        invalid_torrent_groups = [
            {"   ": torrent_models([INITIAL_HASHES[0]])},
            {"UnGrOuPeD": torrent_models([INITIAL_HASHES[0]])},
            {
                "Group A": torrent_models([INITIAL_HASHES[0]]),
                "Group B": torrent_models([INITIAL_HASHES[0]]),
            },
        ]
        for torrents in invalid_torrent_groups:
            with (
                self.subTest(torrents=torrents),
                self.assertRaises(ValidationError),
            ):
                TypeAdapter(list[Aggregate]).validate_python(
                    [{"short_name": "Invalid Import", "torrents": torrents}]
                )

        await self.add_simple_aggregate(client, EMPTY_SHORT_NAME, [])
        await self.add_test_aggregate(client)
        await self.add_simple_aggregate(client, MIXED_SHORT_NAME, MIXED_HASHES)
        await self.add_simple_aggregate(client, GROUPED_SHORT_NAME, GROUPED_HASHES)

        documents_before = await self.search_documents()
        qbit_requests_before = self.mock_server.torrent_info_requests
        await self.update_torrents(
            client,
            MIXED_SHORT_NAME,
            group="Group A",
            add_hashes=MIXED_HASHES[:2],
        )
        await self.update_torrents(
            client,
            GROUPED_SHORT_NAME,
            group="Group A",
            add_hashes=[GROUPED_HASHES[0]],
        )
        await self.update_torrents(
            client,
            GROUPED_SHORT_NAME,
            group="Group B",
            add_hashes=[GROUPED_HASHES[1]],
        )
        self.assertEqual(
            self.mock_server.torrent_info_requests,
            qbit_requests_before,
        )
        self.assertEqual(await self.search_documents(), documents_before)

        aggregates = await self.list_all_aggregates(client)
        by_short_name = {aggregate.short_name: aggregate for aggregate in aggregates}
        last_updated_at = by_short_name[SHORT_NAME].bangumi_subjects[0].last_updated_at
        self.assertEqual(
            by_short_name,
            {
                EMPTY_SHORT_NAME: simple_aggregate(EMPTY_SHORT_NAME, {}),
                SHORT_NAME: expected_aggregate(INITIAL_HASHES, last_updated_at),
                MIXED_SHORT_NAME: simple_aggregate(
                    MIXED_SHORT_NAME,
                    {
                        "ungrouped": torrent_models(MIXED_HASHES[2:]),
                        "Group A": torrent_models(MIXED_HASHES[:2]),
                    },
                ),
                GROUPED_SHORT_NAME: simple_aggregate(
                    GROUPED_SHORT_NAME,
                    {
                        "Group A": torrent_models([GROUPED_HASHES[0]]),
                        "Group B": torrent_models([GROUPED_HASHES[1]]),
                    },
                ),
            },
        )

    async def assert_torrent_group_updates(self, client: Client[Any]) -> None:
        logger.info("test step: move torrents between named and ungrouped buckets")
        request_count = len(self.mock_server.torrent_info_requests)
        await self.update_torrents(
            client,
            MIXED_SHORT_NAME,
            group="Group B",
            add_hashes=[MIXED_HASHES[0]],
        )
        self.assertEqual(len(self.mock_server.torrent_info_requests), request_count)
        await self.update_torrents(
            client,
            MIXED_SHORT_NAME,
            add_hashes=[MIXED_HASHES[1], DIRECT_UNGROUPED_HASH],
        )
        self.assertEqual(
            self.mock_server.torrent_info_requests[request_count:],
            [[DIRECT_UNGROUPED_HASH]],
        )
        result = await self.update_torrents(
            client,
            MIXED_SHORT_NAME,
            group="Group A",
            add_hashes=[DIRECT_GROUP_HASH],
        )
        expected_hashes: dict[str, list[str]] = {
            "ungrouped": [
                MIXED_HASHES[1],
                *MIXED_HASHES[2:],
                DIRECT_UNGROUPED_HASH,
            ],
            "Group A": [DIRECT_GROUP_HASH],
            "Group B": [MIXED_HASHES[0]],
        }
        self.assertEqual(result, expected_hashes)
        self.assertEqual(
            await self.list_aggregate(client, MIXED_SHORT_NAME),
            simple_aggregate(
                MIXED_SHORT_NAME,
                {
                    group_name: torrent_models(hashes)
                    for group_name, hashes in expected_hashes.items()
                },
            ),
        )

        logger.info("test step: remove grouped and ungrouped torrents without a group")
        result = await self.update_torrents(
            client,
            MIXED_SHORT_NAME,
            remove_hashes=[MIXED_HASHES[0], MIXED_HASHES[1]],
        )
        expected_after_remove: dict[str, list[str]] = {
            "ungrouped": [*MIXED_HASHES[2:], DIRECT_UNGROUPED_HASH],
            "Group A": [DIRECT_GROUP_HASH],
        }
        self.assertEqual(result, expected_after_remove)
        self.assertEqual(
            await self.list_aggregate(client, MIXED_SHORT_NAME),
            simple_aggregate(
                MIXED_SHORT_NAME,
                {
                    group_name: torrent_models(hashes)
                    for group_name, hashes in expected_after_remove.items()
                },
            ),
        )

    async def assert_torrent_group_validation(self, client: Client[Any]) -> None:
        logger.info(
            "test step: reject reserved, empty, missing, and cross-aggregate input"
        )
        await self.assert_tool_error(
            client,
            "update_aggregate_torrents",
            {
                "short_name": MIXED_SHORT_NAME,
                "group": "UnGrOuPeD",
                "add_hashes": [DIRECT_GROUP_HASH],
            },
            "reserved",
        )
        await self.assert_tool_error(
            client,
            "update_aggregate_torrents",
            {
                "short_name": MIXED_SHORT_NAME,
                "group": "   ",
                "add_hashes": [DIRECT_GROUP_HASH],
            },
            "cannot be empty",
        )
        await self.assert_tool_error(
            client,
            "update_aggregate_torrents",
            {
                "short_name": MIXED_SHORT_NAME,
                "group": "UnGrOuPeD",
                "remove_hashes": [MIXED_HASHES[2]],
            },
            "reserved",
        )
        await self.assert_tool_error(
            client,
            "update_aggregate_torrents",
            {
                "short_name": MIXED_SHORT_NAME,
                "add_hashes": [DIRECT_GROUP_HASH, DIRECT_GROUP_HASH],
            },
            "add contain duplicates",
        )
        await self.assert_tool_error(
            client,
            "update_aggregate_torrents",
            {
                "short_name": MIXED_SHORT_NAME,
                "remove_hashes": [MIXED_HASHES[2], MIXED_HASHES[2]],
            },
            "remove contain duplicates",
        )
        await self.assert_tool_error(
            client,
            "update_aggregate_torrents",
            {
                "short_name": MIXED_SHORT_NAME,
                "add_hashes": [MIXED_HASHES[2]],
                "remove_hashes": [MIXED_HASHES[2]],
            },
            "same torrent hash",
        )
        await self.assert_tool_error(
            client,
            "update_aggregate_torrents",
            {
                "short_name": MIXED_SHORT_NAME,
                "remove_hashes": ["f" * 40],
            },
            "not found",
        )
        await self.assert_tool_error(
            client,
            "update_aggregate_torrents",
            {
                "short_name": MIXED_SHORT_NAME,
                "group": "Group C",
                "add_hashes": [INITIAL_HASHES[0]],
            },
            SHORT_NAME,
        )

    async def assert_torrent_group_cascade(self, client: Client[Any]) -> None:
        logger.info("test step: aggregate removal cascades torrent group mappings")
        await call_tool(
            client,
            "remove_aggregate",
            {"short_name": GROUPED_SHORT_NAME},
        )
        with sqlite3.connect(self.db_path) as connection:
            group_count = connection.execute(
                "SELECT COUNT(*) FROM torrent_groups WHERE torrent_hash IN (?, ?)",
                GROUPED_HASHES,
            ).fetchone()
        self.assertEqual(group_count, (0,))

    async def add_simple_aggregate(
        self,
        client: Client[Any],
        short_name: str,
        torrent_hashes: list[str],
    ) -> None:
        added = await call_tool(
            client,
            "add_aggregate",
            {"short_name": short_name, "torrent_hashes": torrent_hashes},
        )
        aggregate = ResponsePayload[Aggregate].model_validate(added).data
        expected_torrents: dict[str, list[Torrent]] = {}
        if torrent_hashes:
            expected_torrents["ungrouped"] = torrent_models(torrent_hashes)
        self.assertEqual(
            aggregate,
            simple_aggregate(short_name, expected_torrents),
        )

    async def update_torrents(
        self,
        client: Client[Any],
        short_name: str,
        *,
        group: str | None = None,
        add_hashes: list[str] | None = None,
        remove_hashes: list[str] | None = None,
    ) -> dict[str, list[str]]:
        arguments: dict[str, object] = {"short_name": short_name}
        if group is not None:
            arguments["group"] = group
        if add_hashes is not None:
            arguments["add_hashes"] = add_hashes
        if remove_hashes is not None:
            arguments["remove_hashes"] = remove_hashes
        updated = await call_tool(
            client,
            "update_aggregate_torrents",
            arguments,
        )
        return ResponsePayload[dict[str, list[str]]].model_validate(updated).data

    async def assert_tool_error(
        self,
        client: Client[Any],
        name: str,
        arguments: dict[str, object],
        message: str,
    ) -> None:
        result = await client.call_tool_mcp(name, arguments=arguments)
        self.assertTrue(result.isError)
        self.assertIn(message, str(result.content))

    async def list_all_aggregates(self, client: Client[Any]) -> list[Aggregate]:
        listed = await call_tool(client, "list_aggregates", {})
        return ResponsePayload[list[Aggregate]].model_validate(listed).data

    async def list_aggregate(
        self,
        client: Client[Any],
        short_name: str,
    ) -> Aggregate:
        listed = await call_tool(
            client,
            "list_aggregates",
            {"filter_short_name": [short_name]},
        )
        aggregates = ResponsePayload[list[Aggregate]].model_validate(listed).data
        self.assertEqual(len(aggregates), 1)
        return aggregates[0]

    async def list_test_aggregate(self, client: Client[Any]) -> Aggregate:
        listed = await call_tool(
            client,
            "list_aggregates",
            {"filter_short_name": [SHORT_NAME]},
        )
        listed_aggregates = ResponsePayload[list[Aggregate]].model_validate(listed).data
        self.assertEqual(len(listed_aggregates), 1)
        return listed_aggregates[0]

    async def call_health_check(self, client: Client[Any]) -> HealthCheckReport:
        checked = await call_tool(client, "check_health", {})
        return ResponsePayload[HealthCheckReport].model_validate(checked).data

    async def search_documents(self):
        search_config = replace(
            load_config().search,
            lancedb_path=self.search_path,
        )
        return await asyncio.to_thread(
            LanceDbSearchRepository(search_config).list_documents
        )


def run_health_cli(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "python", "src/main.py", "health"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def run_sync_cli(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "python", "src/main.py", "sync"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def run_audit_cli(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "python", "src/main.py", "audit"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


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
        torrents={"ungrouped": torrent_models(torrent_hashes)}
        if torrent_hashes
        else {},
    )


def simple_aggregate(
    short_name: str,
    torrents: dict[str, list[Torrent]],
) -> Aggregate:
    return Aggregate(short_name=short_name, category="anime", torrents=torrents)


def torrent_models(torrent_hashes: Iterable[str]) -> list[Torrent]:
    return [Torrent(hash=torrent_hash) for torrent_hash in sorted(torrent_hashes)]


def expected_qbittorrent_torrent(torrent_hash: str) -> QbittorrentTorrent:
    return QbittorrentTorrent(
        hash=torrent_hash,
        name=TORRENTS[torrent_hash],
        category="anime",
        save_path="/downloads",
    )


def expected_audit_report(
    tracked_hashes: list[str] | None = None,
) -> AuditReport:
    tracked_hashes = INITIAL_HASHES if tracked_hashes is None else tracked_hashes
    findings = [
        expected_tracked_finding(torrent_hash) for torrent_hash in tracked_hashes
    ]
    findings.extend(
        expected_unmapped_finding(torrent_hash)
        for torrent_hash in reversed(TORRENTS)
        if torrent_hash not in tracked_hashes
    )
    return AuditReport(
        successful=True,
        checks=[
            AuditCheckResult(
                auditor="torrent_mapping",
                status=AuditCheckStatus.COMPLETED,
                findings=findings,
            )
        ],
    )


def expected_tracked_finding(torrent_hash: str) -> AuditFinding:
    torrent = expected_qbittorrent_torrent(torrent_hash)
    return AuditFinding(
        auditor="torrent_mapping",
        code="torrent.tracked_found",
        severity=AuditSeverity.INFO,
        message="Tracked torrent is present in qBittorrent.",
        aggregate_short_name=SHORT_NAME,
        torrent_hash=torrent_hash,
        path=torrent.save_path,
        metadata={
            "aggregates": [SHORT_NAME],
            "torrent_name": torrent.name,
            "category": torrent.category,
        },
    )


def expected_unmapped_finding(torrent_hash: str) -> AuditFinding:
    torrent = expected_qbittorrent_torrent(torrent_hash)
    return AuditFinding(
        auditor="torrent_mapping",
        code="torrent.unmapped",
        severity=AuditSeverity.WARNING,
        message="qBittorrent torrent is not mapped to an aggregate.",
        torrent_hash=torrent_hash,
        path=torrent.save_path,
        metadata={
            "torrent_name": torrent.name,
            "category": torrent.category,
        },
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
