from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from lib.models.audit import AuditReport
from lib.models.health import HealthCheckReport


class SyncStepStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class BangumiCollectionSyncResult(BaseModel):
    step: Literal["bangumi_collections"] = "bangumi_collections"
    status: SyncStepStatus
    reason: str | None = None
    last_successful_sync_at: AwareDatetime | None = None
    next_refresh_at: AwareDatetime | None = None
    fetched: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0
    reactivated: int = 0
    unchanged: int = 0
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        counts = (
            self.fetched,
            self.created,
            self.updated,
            self.removed,
            self.reactivated,
            self.unchanged,
        )
        match self.status:
            case SyncStepStatus.COMPLETED:
                if (
                    self.reason is not None
                    or self.errors
                    or self.last_successful_sync_at is None
                    or self.next_refresh_at is None
                ):
                    raise ValueError(
                        "A completed Bangumi collection step requires refresh "
                        "timestamps and no reason or errors."
                    )
            case SyncStepStatus.SKIPPED:
                if self.reason is None or self.errors or any(counts):
                    raise ValueError(
                        "A skipped Bangumi collection step requires a reason and "
                        "cannot have counts or errors."
                    )
            case SyncStepStatus.FAILED:
                if self.reason is not None or not self.errors or any(counts):
                    raise ValueError(
                        "A failed Bangumi collection step requires errors and cannot "
                        "have a reason or counts."
                    )
        return self


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
    BangumiCollectionSyncResult | SearchIndexSyncResult | AuditSyncResult,
    Field(discriminator="step"),
]


class SyncReport(BaseModel):
    healthy: bool
    health_before: HealthCheckReport
    health_after: HealthCheckReport
    steps: list[SyncStepResult]
