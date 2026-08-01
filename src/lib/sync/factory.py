from __future__ import annotations

from lib.models.health import HealthCheckReport
from lib.sync.context import SyncContext
from lib.sync.protocols import SyncRuntime
from lib.sync.runner import SyncRunner
from lib.sync.steps import SearchIndexSyncStep, TorrentAuditSyncStep


def create_sync_runner(
    runtime: SyncRuntime,
    *,
    force: bool = False,
    audit_qbittorrent: bool = True,
    show_progress: bool = False,
    health_before: HealthCheckReport | None = None,
) -> SyncRunner:
    context = SyncContext(
        runtime=runtime,
        force=force,
        audit_qbittorrent=audit_qbittorrent,
        show_progress=show_progress,
        health_before=health_before,
    )
    return SyncRunner(
        context,
        [SearchIndexSyncStep(), TorrentAuditSyncStep()],
    )
