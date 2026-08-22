from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from config import BangumiConfig
from lib.bangumi import BangumiClient
from lib.models.bangumi import (
    DEFAULT_BANGUMI_COLLECTION_LOCAL_STATES,
    DEFAULT_BANGUMI_COLLECTION_TYPES,
    BangumiCollectionLocalState,
    BangumiCollectionSubjectCoverage,
    BangumiCollectionType,
)
from lib.models.sync import BangumiCollectionSyncResult, SyncStepStatus
from lib.sql.bangumi_collections import SqliteBangumiCollectionRepository
from lib.sql.repositories import SqliteAggregateRepository

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class BangumiCollectionService:
    def __init__(
        self,
        repository: SqliteAggregateRepository,
        bangumi: BangumiClient,
        config: BangumiConfig,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.bangumi = bangumi
        self.config = config
        self.now = now or (lambda: datetime.now(UTC))

    def list_subject_coverage(
        self,
        collection_types: Sequence[BangumiCollectionType] | None = None,
        local_states: Sequence[BangumiCollectionLocalState] | None = None,
    ) -> list[BangumiCollectionSubjectCoverage]:
        """List the configured user's active subjects by local torrent coverage.

        Omitted collection types default to wish and doing. Omitted local states
        default to unmapped and empty. An explicitly empty filter returns no rows.
        """
        username = self.config.username
        if username is None:
            raise ValueError("BANGUMI_USERNAME is not configured.")
        selected_collection_types = tuple(
            DEFAULT_BANGUMI_COLLECTION_TYPES
            if collection_types is None
            else collection_types
        )
        selected_local_states = tuple(
            DEFAULT_BANGUMI_COLLECTION_LOCAL_STATES
            if local_states is None
            else local_states
        )
        with self.repository.get_repository(write=False) as aggregate_repository:
            return SqliteBangumiCollectionRepository(
                aggregate_repository.require_session()
            ).list_subject_coverage(
                username,
                selected_collection_types,
                selected_local_states,
            )

    def sync(self, *, force: bool = False) -> BangumiCollectionSyncResult:
        username = self.config.username
        if username is None:
            return BangumiCollectionSyncResult(
                status=SyncStepStatus.SKIPPED,
                reason="BANGUMI_USERNAME is not configured.",
            )

        with self.repository.get_repository(write=False) as aggregate_repository:
            collection_repository = SqliteBangumiCollectionRepository(
                aggregate_repository.require_session()
            )
            state = collection_repository.get_sync_state(username)

        checked_at = self.now()
        if state is not None:
            next_refresh_at = state.last_successful_sync_at + self.config.collection_ttl
            if not force and checked_at < next_refresh_at:
                return BangumiCollectionSyncResult(
                    status=SyncStepStatus.SKIPPED,
                    reason="Bangumi collection mirror is still fresh.",
                    last_successful_sync_at=state.last_successful_sync_at,
                    next_refresh_at=next_refresh_at,
                )

        collections = self.bangumi.get_user_collections(username)
        synced_at = self.now()
        with self.repository.get_repository(write=True) as aggregate_repository:
            collection_repository = SqliteBangumiCollectionRepository(
                aggregate_repository.require_session()
            )
            changes = collection_repository.synchronize(
                username,
                collections,
                synced_at,
            )

        return BangumiCollectionSyncResult(
            status=SyncStepStatus.COMPLETED,
            last_successful_sync_at=synced_at,
            next_refresh_at=synced_at + self.config.collection_ttl,
            fetched=changes.fetched,
            created=changes.created,
            updated=changes.updated,
            removed=changes.removed,
            reactivated=changes.reactivated,
            unchanged=changes.unchanged,
        )
