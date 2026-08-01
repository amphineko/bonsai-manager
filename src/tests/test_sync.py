from __future__ import annotations

import unittest

from pydantic import ValidationError
from requests import RequestException

from lib.models.health import HealthCheckReport, SearchIndexConsistencyCheck
from lib.models.qbittorrent import TorrentMappingAudit
from lib.models.search import AggregateSearchDocument
from lib.models.sync import (
    SearchIndexSyncResult,
    SyncReport,
    SyncStepStatus,
    TorrentAuditSyncResult,
)
from lib.sync import create_sync_runner


class FakeSyncRuntime:
    def __init__(
        self,
        health_reports: list[HealthCheckReport],
        *,
        rebuild_error: RuntimeError | None = None,
        audit_error: RequestException | None = None,
    ) -> None:
        self.health_reports = health_reports
        self.rebuild_error = rebuild_error
        self.audit_error = audit_error
        self.health_calls = 0
        self.rebuild_calls: list[tuple[bool, bool]] = []
        self.audit_calls = 0
        self.events: list[str] = []

    def check_health(self) -> HealthCheckReport:
        self.events.append("health")
        report = self.health_reports[self.health_calls]
        self.health_calls += 1
        return report

    def rebuild_search_index(
        self,
        force: bool = False,
        show_progress: bool = False,
    ) -> list[AggregateSearchDocument]:
        self.events.append("search")
        self.rebuild_calls.append((force, show_progress))
        if self.rebuild_error is not None:
            raise self.rebuild_error
        return [search_document_fixture()]

    def audit_torrent_mapping(
        self,
        categories: list[str] | None = None,
    ) -> TorrentMappingAudit:
        self.events.append("audit")
        self.audit_calls += 1
        if self.audit_error is not None:
            raise self.audit_error
        return TorrentMappingAudit()


class SyncRunnerTest(unittest.TestCase):
    def test_sync_runs_steps_in_order(self) -> None:
        before = health_report(healthy=False, missing_documents=["Fixture"])
        after = health_report(healthy=True)
        runtime = FakeSyncRuntime([before, after])

        report = create_sync_runner(
            runtime,
            force=True,
            show_progress=True,
        ).run()

        self.assertEqual(
            report,
            SyncReport(
                healthy=True,
                health_before=before,
                health_after=after,
                steps=[
                    SearchIndexSyncResult(
                        status=SyncStepStatus.COMPLETED,
                        indexed_documents=1,
                        force=True,
                    ),
                    TorrentAuditSyncResult(
                        status=SyncStepStatus.COMPLETED,
                        report=TorrentMappingAudit(),
                    ),
                ],
            ),
        )
        self.assertEqual(runtime.rebuild_calls, [(True, True)])
        self.assertEqual(runtime.audit_calls, 1)
        self.assertEqual(runtime.events, ["health", "search", "audit", "health"])

    def test_sync_reports_expected_operational_errors(self) -> None:
        unhealthy = health_report(healthy=False, missing_documents=["Fixture"])
        runtime = FakeSyncRuntime(
            [unhealthy, unhealthy],
            rebuild_error=RuntimeError("embedding unavailable"),
            audit_error=RequestException("qBittorrent unavailable"),
        )

        report = create_sync_runner(runtime).run()

        self.assertEqual(
            report,
            SyncReport(
                healthy=False,
                health_before=unhealthy,
                health_after=unhealthy,
                steps=[
                    SearchIndexSyncResult(
                        status=SyncStepStatus.FAILED,
                        force=False,
                        errors=["RuntimeError: embedding unavailable"],
                    ),
                    TorrentAuditSyncResult(
                        status=SyncStepStatus.FAILED,
                        errors=["RequestException: qBittorrent unavailable"],
                    ),
                ],
            ),
        )
        self.assertEqual(runtime.events, ["health", "search", "audit", "health"])

    def test_sync_can_skip_qbittorrent_audit_and_reuse_preflight_health(self) -> None:
        healthy = health_report(healthy=True)
        runtime = FakeSyncRuntime([healthy])

        report = create_sync_runner(
            runtime,
            audit_qbittorrent=False,
            health_before=healthy,
        ).run()

        self.assertTrue(report.healthy)
        self.assertEqual(
            report.steps[1],
            TorrentAuditSyncResult(status=SyncStepStatus.SKIPPED),
        )
        self.assertEqual(runtime.audit_calls, 0)
        self.assertEqual(runtime.events, ["search", "health"])

    def test_step_results_reject_contradictory_states(self) -> None:
        with self.assertRaises(ValidationError):
            SearchIndexSyncResult(
                status=SyncStepStatus.COMPLETED,
                force=False,
                errors=["unexpected"],
            )
        with self.assertRaises(ValidationError):
            TorrentAuditSyncResult(status=SyncStepStatus.FAILED)


def health_report(
    *,
    healthy: bool,
    missing_documents: list[str] | None = None,
) -> HealthCheckReport:
    missing_documents = missing_documents or []
    aggregate_count = 1 if missing_documents else 0
    return HealthCheckReport(
        healthy=healthy,
        checks=[
            SearchIndexConsistencyCheck(
                healthy=healthy,
                aggregate_count=aggregate_count,
                document_count=0,
                missing_documents=missing_documents,
                orphaned_documents=[],
                stale_documents=[],
                duplicate_documents=[],
            )
        ],
    )


def search_document_fixture() -> AggregateSearchDocument:
    return AggregateSearchDocument(
        aggregate_short_name="Fixture",
        source_text="short_name: Fixture",
        source_hash="sha256:fixture",
        embedding=[1.0],
        model_name="fixture-model",
        updated_at="2026-08-02T00:00:00+00:00",
    )


if __name__ == "__main__":
    unittest.main()
