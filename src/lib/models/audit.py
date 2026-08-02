from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class AuditSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AuditCheckStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class AuditFinding(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    auditor: str
    code: str
    severity: AuditSeverity
    message: str
    aggregate_short_name: str | None = None
    torrent_hash: str | None = None
    path: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AuditCheckResult(BaseModel):
    auditor: str
    status: AuditCheckStatus
    findings: list[AuditFinding] = Field(default_factory=list)
    skip_reason: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        match self.status:
            case AuditCheckStatus.COMPLETED:
                if self.skip_reason is not None or self.error is not None:
                    raise ValueError(
                        "A completed audit check cannot have a skip reason or error."
                    )
            case AuditCheckStatus.SKIPPED:
                if self.findings or not self.skip_reason or self.error is not None:
                    raise ValueError(
                        "A skipped audit check requires a reason and no findings or "
                        "error."
                    )
            case AuditCheckStatus.FAILED:
                if self.findings or self.skip_reason is not None or self.error is None:
                    raise ValueError(
                        "A failed audit check requires an error and no findings or "
                        "skip reason."
                    )
        if any(finding.auditor != self.auditor for finding in self.findings):
            raise ValueError("Audit findings must match their check auditor.")
        return self


class AuditReport(BaseModel):
    successful: bool
    checks: list[AuditCheckResult]

    @model_validator(mode="after")
    def validate_successful(self) -> Self:
        expected = all(check.status != AuditCheckStatus.FAILED for check in self.checks)
        if self.successful != expected:
            raise ValueError("Audit report success does not match its check results.")
        return self

    def all_findings(self) -> list[AuditFinding]:
        return [finding for check in self.checks for finding in check.findings]
