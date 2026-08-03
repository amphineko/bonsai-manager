from __future__ import annotations

from typing import TYPE_CHECKING

from requests import RequestException
from sqlalchemy.exc import SQLAlchemyError

from lib.audit.context import AuditContext
from lib.audit.exceptions import AuditExecutionError, AuditSkipped
from lib.audit.protocols import AggregateAuditor
from lib.models.audit import (
    AuditCheckResult,
    AuditCheckStatus,
    AuditReport,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class AuditRunner:
    def __init__(
        self,
        context: AuditContext,
        auditors: Sequence[AggregateAuditor],
    ) -> None:
        self.context = context
        self.auditors = tuple(auditors)

    def run(self) -> AuditReport:
        checks = [self._run_auditor(auditor) for auditor in self.auditors]
        return AuditReport(
            successful=all(check.status != AuditCheckStatus.FAILED for check in checks),
            checks=checks,
        )

    def _run_auditor(self, auditor: AggregateAuditor) -> AuditCheckResult:
        try:
            findings = auditor.audit(self.context)
            return AuditCheckResult(
                auditor=auditor.name,
                status=AuditCheckStatus.COMPLETED,
                findings=findings,
            )
        except AuditSkipped as exc:
            return AuditCheckResult(
                auditor=auditor.name,
                status=AuditCheckStatus.SKIPPED,
                skip_reason=str(exc),
            )
        except (
            AuditExecutionError,
            OSError,
            RequestException,
            RuntimeError,
            SQLAlchemyError,
            ValueError,
        ) as exc:
            return AuditCheckResult(
                auditor=auditor.name,
                status=AuditCheckStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
