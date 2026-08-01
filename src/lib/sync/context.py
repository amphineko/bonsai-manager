from __future__ import annotations

from dataclasses import dataclass

from lib.models.health import HealthCheckReport
from lib.sync.protocols import SyncRuntime


@dataclass(frozen=True)
class SyncContext:
    runtime: SyncRuntime
    force: bool = False
    audit_qbittorrent: bool = True
    show_progress: bool = False
    health_before: HealthCheckReport | None = None
