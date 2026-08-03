from __future__ import annotations

from requests import RequestException

from lib.models.audit import AuditCheckResult, AuditCheckStatus, AuditReport
from lib.models.sync import AuditSyncResult, SyncStepStatus
from lib.sync.context import SyncContext


class AuditSyncStep:
    def run(self, context: SyncContext) -> AuditSyncResult:
        if not context.audit_enabled:
            return AuditSyncResult(status=SyncStepStatus.SKIPPED)

        try:
            report = context.runtime.run_audit()
        except (
            ImportError,
            OSError,
            RequestException,
            RuntimeError,
            ValueError,
        ) as exc:
            error = f"{type(exc).__name__}: {exc}"
            report = AuditReport(
                successful=False,
                checks=[
                    AuditCheckResult(
                        auditor="audit_setup",
                        status=AuditCheckStatus.FAILED,
                        error=error,
                    )
                ],
            )
            return AuditSyncResult(
                status=SyncStepStatus.FAILED,
                report=report,
                errors=[error],
            )
        if not report.successful:
            return AuditSyncResult(
                status=SyncStepStatus.FAILED,
                report=report,
                errors=[
                    check.error for check in report.checks if check.error is not None
                ],
            )

        return AuditSyncResult(
            status=SyncStepStatus.COMPLETED,
            report=report,
        )
