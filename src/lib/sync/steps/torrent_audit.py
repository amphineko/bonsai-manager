from __future__ import annotations

from requests import RequestException

from lib.models.sync import SyncStepStatus, TorrentAuditSyncResult
from lib.sync.context import SyncContext


class TorrentAuditSyncStep:
    def run(self, context: SyncContext) -> TorrentAuditSyncResult:
        if not context.audit_qbittorrent:
            return TorrentAuditSyncResult(status=SyncStepStatus.SKIPPED)

        try:
            report = context.runtime.audit_torrent_mapping()
        except (OSError, RequestException, RuntimeError, ValueError) as exc:
            return TorrentAuditSyncResult(
                status=SyncStepStatus.FAILED,
                errors=[f"{type(exc).__name__}: {exc}"],
            )

        return TorrentAuditSyncResult(
            status=SyncStepStatus.COMPLETED,
            report=report,
        )
