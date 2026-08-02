from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from lib.models.audit import AuditReport
from lib.models.health import HealthCheckReport
from lib.models.search import AggregateSearchDocument

if TYPE_CHECKING:
    from lib.models.sync import SyncStepResult
    from lib.sync.context import SyncContext


class SyncRuntime(Protocol):
    def check_health(self) -> HealthCheckReport: ...

    def rebuild_search_index(
        self,
        force: bool = False,
        show_progress: bool = False,
    ) -> list[AggregateSearchDocument]: ...

    def run_audit(
        self,
        categories: list[str] | None = None,
    ) -> AuditReport: ...


class SyncStep(Protocol):
    def run(self, context: SyncContext) -> SyncStepResult: ...
