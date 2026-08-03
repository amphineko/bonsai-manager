from __future__ import annotations

import unittest
from typing import TYPE_CHECKING, cast, override

from pydantic import ValidationError

from lib.audit.checks import TorrentMappingAuditor
from lib.audit.context import AuditContext
from lib.audit.exceptions import AuditExecutionError, AuditSkipped
from lib.audit.factory import create_audit_runner
from lib.audit.runner import AuditRunner
from lib.models.aggregates import Aggregate, Torrent
from lib.models.audit import (
    AuditCheckResult,
    AuditCheckStatus,
    AuditFinding,
    AuditReport,
    AuditSeverity,
)
from lib.models.qbittorrent import QbittorrentTorrent, QbittorrentTorrentFile
from lib.sql.repositories import SqliteAggregateRepository

if TYPE_CHECKING:
    from pydantic import JsonValue

HASH_A = "a" * 40
HASH_B = "b" * 40
HASH_C = "c" * 40
HASH_D = "d" * 40


class StubAuditQbittorrentClient:
    def __init__(self, torrents: list[QbittorrentTorrent]) -> None:
        self.torrents = torrents
        self.login_calls = 0
        self.torrent_calls = 0
        self.file_calls: list[str] = []

    def login(self) -> None:
        self.login_calls += 1

    def get_all_torrents(self) -> list[QbittorrentTorrent]:
        self.torrent_calls += 1
        return self.torrents

    def get_torrent_files(
        self,
        torrent_hash: str,
    ) -> list[QbittorrentTorrentFile]:
        self.file_calls.append(torrent_hash)
        return [QbittorrentTorrentFile(name=f"{torrent_hash}.mkv")]


class FailingAuditor:
    name = "failing"

    def audit(self, context: AuditContext) -> list[AuditFinding]:
        raise AuditExecutionError("plugin unavailable")


class PassingAuditor:
    name = "passing"

    def audit(self, context: AuditContext) -> list[AuditFinding]:
        return [
            AuditFinding(
                auditor=self.name,
                code="fixture.passed",
                severity=AuditSeverity.INFO,
                message="Fixture passed.",
            )
        ]


class SkippedAuditor:
    name = "skipped"

    def audit(self, context: AuditContext) -> list[AuditFinding]:
        raise AuditSkipped("fixture is not configured")


class MismatchedAuditor:
    name = "mismatched"

    def audit(self, context: AuditContext) -> list[AuditFinding]:
        return [
            AuditFinding(
                auditor="wrong",
                code="fixture.mismatched",
                severity=AuditSeverity.ERROR,
                message="Fixture mismatch.",
            )
        ]


class AuditFrameworkTest(unittest.TestCase):
    repository: SqliteAggregateRepository
    qbit: StubAuditQbittorrentClient
    context: AuditContext

    @override
    def setUp(self) -> None:
        self.repository = SqliteAggregateRepository(":memory:")
        with self.repository.get_repository(write=True) as repo:
            repo.add(
                Aggregate(
                    short_name="Fixture",
                    torrents={
                        "ungrouped": [Torrent(hash=HASH_A), Torrent(hash=HASH_B)]
                    },
                )
            )
        self.qbit = StubAuditQbittorrentClient(
            [
                qbit_torrent(HASH_A, "Tracked", "anime"),
                qbit_torrent(HASH_C, "Unmapped", "anime"),
                qbit_torrent(HASH_D, "Ignored", "other"),
            ]
        )
        self.context = AuditContext(
            repository=self.repository,
            qbit=self.qbit,
            categories=("anime",),
        )

    @override
    def tearDown(self) -> None:
        self.repository.close()

    def test_torrent_mapping_auditor_preserves_mapping_semantics(self) -> None:
        report = AuditRunner(self.context, [TorrentMappingAuditor()]).run()

        self.assertEqual(
            report,
            AuditReport(
                successful=True,
                checks=[
                    AuditCheckResult(
                        auditor="torrent_mapping",
                        status=AuditCheckStatus.COMPLETED,
                        findings=[
                            mapping_finding(
                                code="torrent.tracked_found",
                                severity=AuditSeverity.INFO,
                                message="Tracked torrent is present in qBittorrent.",
                                torrent_hash=HASH_A,
                                torrent_name="Tracked",
                                path="/downloads",
                            ),
                            mapping_finding(
                                code="torrent.tracked_missing",
                                severity=AuditSeverity.WARNING,
                                message=(
                                    "Tracked torrent is missing from qBittorrent."
                                ),
                                torrent_hash=HASH_B,
                            ),
                            AuditFinding(
                                auditor="torrent_mapping",
                                code="torrent.unmapped",
                                severity=AuditSeverity.WARNING,
                                message=(
                                    "qBittorrent torrent is not mapped to an aggregate."
                                ),
                                torrent_hash=HASH_C,
                                path="/downloads",
                                metadata={
                                    "torrent_name": "Unmapped",
                                    "category": "anime",
                                },
                            ),
                        ],
                    )
                ],
            ),
        )

    def test_context_caches_shared_data_and_authenticates_once(self) -> None:
        aggregates = self.context.get_aggregates()
        with self.repository.get_repository(write=True) as repo:
            repo.add(Aggregate(short_name="Added Later"))

        self.assertIs(self.context.get_aggregates(), aggregates)
        self.assertIsInstance(aggregates, tuple)
        self.assertEqual(len(self.context.get_aggregates()), 1)
        torrents = self.context.get_qbittorrent_torrents()
        files = self.context.get_torrent_files(HASH_A)
        self.assertIs(torrents, self.context.get_qbittorrent_torrents())
        self.assertIs(files, self.context.get_torrent_files(HASH_A))
        self.assertIsInstance(torrents, tuple)
        self.assertIsInstance(files, tuple)
        self.assertEqual(self.qbit.login_calls, 1)
        self.assertEqual(self.qbit.torrent_calls, 1)
        self.assertEqual(self.qbit.file_calls, [HASH_A])

    def test_torrent_mapping_auditor_reports_duplicate_locations(self) -> None:
        self.context._aggregates = (
            Aggregate(
                short_name="Fixture A",
                torrents={"ungrouped": [Torrent(hash=HASH_A)]},
            ),
            Aggregate(
                short_name="Fixture B",
                torrents={"ungrouped": [Torrent(hash=HASH_A)]},
            ),
        )

        findings = TorrentMappingAuditor().audit(self.context)

        self.assertEqual(
            findings[0],
            AuditFinding(
                auditor="torrent_mapping",
                code="torrent.duplicate_mapping",
                severity=AuditSeverity.ERROR,
                message="Torrent is mapped to multiple aggregates.",
                torrent_hash=HASH_A,
                metadata={"aggregates": ["Fixture A", "Fixture B"]},
            ),
        )

    def test_runner_isolates_operational_failures_between_auditors(self) -> None:
        report = AuditRunner(
            self.context,
            [FailingAuditor(), SkippedAuditor(), PassingAuditor()],
        ).run()

        self.assertEqual(
            report,
            AuditReport(
                successful=False,
                checks=[
                    AuditCheckResult(
                        auditor="failing",
                        status=AuditCheckStatus.FAILED,
                        error="AuditExecutionError: plugin unavailable",
                    ),
                    AuditCheckResult(
                        auditor="skipped",
                        status=AuditCheckStatus.SKIPPED,
                        skip_reason="fixture is not configured",
                    ),
                    AuditCheckResult(
                        auditor="passing",
                        status=AuditCheckStatus.COMPLETED,
                        findings=[
                            AuditFinding(
                                auditor="passing",
                                code="fixture.passed",
                                severity=AuditSeverity.INFO,
                                message="Fixture passed.",
                            )
                        ],
                    ),
                ],
            ),
        )

    def test_runner_isolates_invalid_plugin_results(self) -> None:
        report = AuditRunner(
            self.context,
            [MismatchedAuditor(), PassingAuditor()],
        ).run()

        self.assertFalse(report.successful)
        self.assertEqual(report.checks[0].status, AuditCheckStatus.FAILED)
        self.assertIn("ValidationError", report.checks[0].error or "")
        self.assertEqual(report.checks[1].status, AuditCheckStatus.COMPLETED)

    def test_factory_reports_unknown_auditor_names(self) -> None:
        report = create_audit_runner(
            self.repository,
            self.qbit,
            categories=("anime",),
            auditor_names=("unknown",),
        ).run()

        self.assertEqual(
            report,
            AuditReport(
                successful=False,
                checks=[
                    AuditCheckResult(
                        auditor="unknown",
                        status=AuditCheckStatus.FAILED,
                        error="ValueError: Unknown aggregate auditor: unknown",
                    )
                ],
            ),
        )

    def test_finding_metadata_rejects_non_finite_numbers(self) -> None:
        with self.assertRaises(ValidationError):
            AuditFinding(
                auditor="fixture",
                code="fixture.invalid_number",
                severity=AuditSeverity.ERROR,
                message="Fixture invalid number.",
                metadata={"value": float("nan")},
            )


def qbit_torrent(
    torrent_hash: str,
    name: str,
    category: str,
) -> QbittorrentTorrent:
    return QbittorrentTorrent(
        hash=torrent_hash,
        name=name,
        category=category,
        save_path="/downloads",
    )


def mapping_finding(
    *,
    code: str,
    severity: AuditSeverity,
    message: str,
    torrent_hash: str,
    torrent_name: str | None = None,
    path: str | None = None,
) -> AuditFinding:
    metadata: dict[str, JsonValue] = {"aggregates": cast("JsonValue", ["Fixture"])}
    if torrent_name is not None:
        metadata.update(
            {
                "torrent_name": torrent_name,
                "category": "anime",
            }
        )
    return AuditFinding(
        auditor="torrent_mapping",
        code=code,
        severity=severity,
        message=message,
        aggregate_short_name="Fixture",
        torrent_hash=torrent_hash,
        path=path,
        metadata=metadata,
    )


if __name__ == "__main__":
    unittest.main()
