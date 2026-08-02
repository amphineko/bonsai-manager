from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator

from lib.models.audit import AuditReport
from lib.models.health import HealthCheckReport


class SyncStepStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class SearchIndexSyncResult(BaseModel):
    step: Literal["search_index"] = "search_index"
    status: SyncStepStatus
    indexed_documents: int = 0
    force: bool
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        match self.status:
            case SyncStepStatus.COMPLETED:
                if self.errors:
                    raise ValueError(
                        "A completed search index step cannot have errors."
                    )
            case SyncStepStatus.SKIPPED:
                if self.indexed_documents or self.errors:
                    raise ValueError(
                        "A skipped search index step cannot have output or errors."
                    )
            case SyncStepStatus.FAILED:
                if self.indexed_documents or not self.errors:
                    raise ValueError(
                        "A failed search index step requires errors and no output."
                    )
        return self


class AuditSyncResult(BaseModel):
    step: Literal["audit"] = "audit"
    status: SyncStepStatus
    report: AuditReport | None = None
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        match self.status:
            case SyncStepStatus.COMPLETED:
                if self.report is None or not self.report.successful or self.errors:
                    raise ValueError(
                        "A completed audit step requires a successful report and no "
                        "errors."
                    )
            case SyncStepStatus.SKIPPED:
                if self.report is not None or self.errors:
                    raise ValueError(
                        "A skipped audit step cannot have output or errors."
                    )
            case SyncStepStatus.FAILED:
                if self.report is None or self.report.successful or not self.errors:
                    raise ValueError(
                        "A failed audit step requires a failed report and errors."
                    )
        return self


type SyncStepResult = Annotated[
    SearchIndexSyncResult | AuditSyncResult,
    Field(discriminator="step"),
]


class SyncReport(BaseModel):
    healthy: bool
    health_before: HealthCheckReport
    health_after: HealthCheckReport
    steps: list[SyncStepResult]
