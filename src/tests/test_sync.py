from __future__ import annotations

import unittest

from pydantic import ValidationError

from lib.models.audit import (
    AuditCheckResult,
    AuditCheckStatus,
    AuditReport,
)
from lib.models.health import HealthCheckReport, SearchIndexConsistencyCheck
from lib.models.search import AggregateSearchDocument
from lib.models.sync import (
    AuditSyncResult,
    BangumiCollectionSyncResult,
    SearchIndexSyncResult,
    SyncReport,
    SyncStepStatus,
)
from lib.sync import create_sync_runner


class FakeSyncRuntime:
    def __init__(
        self,
        health_reports: list[HealthCheckReport],
        *,
        rebuild_error: RuntimeError | None = None,
        audit_report: AuditReport | None = None,
        audit_error: ValueError | None = None,
        collection_result: BangumiCollectionSyncResult | None = None,
        collection_error: ValueError | None = None,
    ) -> None:
        self.health_reports = health_reports
        self.rebuild_error = rebuild_error
        self.audit_report = audit_report or successful_audit_report()
        self.audit_error = audit_error
        self.collection_result = collection_result or skipped_collection_result()
        self.collection_error = collection_error
        self.health_calls = 0
        self.rebuild_calls: list[tuple[bool, bool]] = []
        self.audit_calls = 0
        self.events: list[str] = []

    def sync_bangumi_collections(
        self,
        *,
        force: bool = False,
    ) -> BangumiCollectionSyncResult:
        self.events.append(f"bangumi:{force}")
        if self.collection_error is not None:
            raise self.collection_error
        return self.collection_result

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

    def run_audit(
        self,
        categories: list[str] | None = None,
    ) -> AuditReport:
        self.events.append("audit")
        self.audit_calls += 1
        if self.audit_error is not None:
            raise self.audit_error
        return self.audit_report


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
                    skipped_collection_result(),
                    SearchIndexSyncResult(
                        status=SyncStepStatus.COMPLETED,
                        indexed_documents=1,
                        force=True,
                    ),
                    AuditSyncResult(
                        status=SyncStepStatus.COMPLETED,
                        report=successful_audit_report(),
                    ),
                ],
            ),
        )
        self.assertEqual(runtime.rebuild_calls, [(True, True)])
        self.assertEqual(runtime.audit_calls, 1)
        self.assertEqual(
            runtime.events,
            ["health", "bangumi:True", "search", "audit", "health"],
        )

    def test_sync_reports_expected_operational_errors(self) -> None:
        unhealthy = health_report(healthy=False, missing_documents=["Fixture"])
        failed_audit = failed_audit_report()
        runtime = FakeSyncRuntime(
            [unhealthy, unhealthy],
            rebuild_error=RuntimeError("embedding unavailable"),
            audit_report=failed_audit,
        )

        report = create_sync_runner(runtime).run()

        self.assertEqual(
            report,
            SyncReport(
                healthy=False,
                health_before=unhealthy,
                health_after=unhealthy,
                steps=[
                    skipped_collection_result(),
                    SearchIndexSyncResult(
                        status=SyncStepStatus.FAILED,
                        force=False,
                        errors=["RuntimeError: embedding unavailable"],
                    ),
                    AuditSyncResult(
                        status=SyncStepStatus.FAILED,
                        report=failed_audit,
                        errors=["RequestException: qBittorrent unavailable"],
                    ),
                ],
            ),
        )
        self.assertEqual(
            runtime.events,
            ["health", "bangumi:False", "search", "audit", "health"],
        )

    def test_sync_can_skip_audit_and_reuse_preflight_health(self) -> None:
        healthy = health_report(healthy=True)
        runtime = FakeSyncRuntime([healthy])

        report = create_sync_runner(
            runtime,
            audit_enabled=False,
            health_before=healthy,
        ).run()

        self.assertTrue(report.healthy)
        self.assertEqual(
            report.steps[2],
            AuditSyncResult(status=SyncStepStatus.SKIPPED),
        )
        self.assertEqual(runtime.audit_calls, 0)
        self.assertEqual(runtime.events, ["bangumi:False", "search", "health"])

    def test_sync_normalizes_audit_setup_errors_and_checks_health_after(self) -> None:
        healthy = health_report(healthy=True)
        runtime = FakeSyncRuntime(
            [healthy, healthy],
            audit_error=ValueError("Unknown aggregate auditor: typo"),
        )

        report = create_sync_runner(runtime).run()

        self.assertFalse(report.healthy)
        audit_result = report.steps[2]
        self.assertIsInstance(audit_result, AuditSyncResult)
        self.assertEqual(audit_result.status, SyncStepStatus.FAILED)
        self.assertEqual(
            audit_result.errors,
            ["ValueError: Unknown aggregate auditor: typo"],
        )
        self.assertEqual(
            runtime.events,
            ["health", "bangumi:False", "search", "audit", "health"],
        )

    def test_step_results_reject_contradictory_states(self) -> None:
        with self.assertRaises(ValidationError):
            SearchIndexSyncResult(
                status=SyncStepStatus.COMPLETED,
                force=False,
                errors=["unexpected"],
            )
        with self.assertRaises(ValidationError):
            AuditSyncResult(status=SyncStepStatus.FAILED)

    def test_sync_isolates_bangumi_collection_errors(self) -> None:
        healthy = health_report(healthy=True)
        runtime = FakeSyncRuntime(
            [healthy, healthy],
            collection_error=ValueError("incomplete page"),
        )

        report = create_sync_runner(runtime).run()

        self.assertFalse(report.healthy)
        self.assertEqual(
            report.steps[0],
            BangumiCollectionSyncResult(
                status=SyncStepStatus.FAILED,
                errors=["ValueError: incomplete page"],
            ),
        )
        self.assertEqual(
            runtime.events,
            ["health", "bangumi:False", "search", "audit", "health"],
        )


def successful_audit_report() -> AuditReport:
    return AuditReport(
        successful=True,
        checks=[
            AuditCheckResult(
                auditor="fixture",
                status=AuditCheckStatus.COMPLETED,
            )
        ],
    )


def skipped_collection_result() -> BangumiCollectionSyncResult:
    return BangumiCollectionSyncResult(
        status=SyncStepStatus.SKIPPED,
        reason="BANGUMI_USERNAME is not configured.",
    )


def failed_audit_report() -> AuditReport:
    return AuditReport(
        successful=False,
        checks=[
            AuditCheckResult(
                auditor="torrent_mapping",
                status=AuditCheckStatus.FAILED,
                error="RequestException: qBittorrent unavailable",
            )
        ],
    )


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
