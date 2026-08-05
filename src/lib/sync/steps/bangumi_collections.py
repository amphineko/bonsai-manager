from requests import RequestException
from sqlalchemy.exc import SQLAlchemyError

from lib.models.sync import BangumiCollectionSyncResult, SyncStepStatus
from lib.sync.context import SyncContext


class BangumiCollectionSyncStep:
    def run(self, context: SyncContext) -> BangumiCollectionSyncResult:
        try:
            return context.runtime.sync_bangumi_collections(force=context.force)
        except (
            ImportError,
            OSError,
            RequestException,
            SQLAlchemyError,
            RuntimeError,
            ValueError,
        ) as exc:
            return BangumiCollectionSyncResult(
                status=SyncStepStatus.FAILED,
                errors=[f"{type(exc).__name__}: {exc}"],
            )
