from __future__ import annotations

from typing import TYPE_CHECKING

from lib.models.sync import SyncReport, SyncStepStatus
from lib.sync.context import SyncContext
from lib.sync.protocols import SyncStep

if TYPE_CHECKING:
    from collections.abc import Sequence


class SyncRunner:
    def __init__(self, context: SyncContext, steps: Sequence[SyncStep]) -> None:
        self.context = context
        self.steps = tuple(steps)

    def run(self) -> SyncReport:
        health_before = (
            self.context.health_before or self.context.runtime.check_health()
        )
        results = [step.run(self.context) for step in self.steps]
        health_after = self.context.runtime.check_health()
        steps_succeeded = all(
            result.status != SyncStepStatus.FAILED for result in results
        )
        return SyncReport(
            healthy=health_after.healthy and steps_succeeded,
            health_before=health_before,
            health_after=health_after,
            steps=results,
        )
