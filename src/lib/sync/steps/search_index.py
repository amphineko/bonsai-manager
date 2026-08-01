from __future__ import annotations

from lib.models.sync import SearchIndexSyncResult, SyncStepStatus
from lib.sync.context import SyncContext


class SearchIndexSyncStep:
    def run(self, context: SyncContext) -> SearchIndexSyncResult:
        try:
            documents = context.runtime.rebuild_search_index(
                force=context.force,
                show_progress=context.show_progress,
            )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            return SearchIndexSyncResult(
                status=SyncStepStatus.FAILED,
                force=context.force,
                errors=[f"{type(exc).__name__}: {exc}"],
            )

        return SearchIndexSyncResult(
            status=SyncStepStatus.COMPLETED,
            indexed_documents=len(documents),
            force=context.force,
        )
