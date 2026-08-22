from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select

from lib.models.bangumi import (
    BangumiCollectionAggregateCoverage,
    BangumiCollectionLocalState,
    BangumiCollectionSubjectCoverage,
    BangumiCollectionSyncState,
    BangumiCollectionType,
    BangumiRemoteCollection,
    BangumiSubject,
    BangumiUserCollection,
)
from lib.sql.repositories import (
    AggregateBangumiSubjectRow,
    AggregateRow,
    BangumiCollectionSyncStateRow,
    BangumiSubjectRow,
    BangumiUserCollectionRow,
    TorrentRow,
    bangumi_subject_from_row,
    upsert_subject_row,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class BangumiCollectionChanges:
    fetched: int
    created: int
    updated: int
    removed: int
    reactivated: int
    unchanged: int


class SqliteBangumiCollectionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_sync_state(self, username: str) -> BangumiCollectionSyncState | None:
        row = self.session.get(BangumiCollectionSyncStateRow, username)
        if row is None:
            return None
        return BangumiCollectionSyncState(
            username=row.username,
            last_successful_sync_at=datetime.fromisoformat(row.last_successful_sync_at),
        )

    def list_for_user(self, username: str) -> list[BangumiUserCollection]:
        rows = list(
            self.session.scalars(
                select(BangumiUserCollectionRow)
                .where(BangumiUserCollectionRow.username == username)
                .order_by(BangumiUserCollectionRow.subject_id)
            )
        )
        return [collection_from_row(row) for row in rows]

    def list_subject_coverage(
        self,
        username: str,
        collection_types: tuple[BangumiCollectionType, ...],
        local_states: tuple[BangumiCollectionLocalState, ...],
    ) -> list[BangumiCollectionSubjectCoverage]:
        """Query active collection subjects matching explicit coverage filters.

        Coverage is calculated across every Aggregate linked to a subject. A subject
        is unmapped when it has no Aggregate links, empty when all linked Aggregates
        have no torrents, and with_torrents when any linked Aggregate has a torrent.
        Soft-removed collection rows are excluded.
        """
        if not collection_types or not local_states:
            return []

        mapping_exists = (
            select(AggregateBangumiSubjectRow.subject_id)
            .where(
                AggregateBangumiSubjectRow.subject_id
                == BangumiUserCollectionRow.subject_id
            )
            .exists()
        )
        torrent_exists = (
            select(TorrentRow.hash)
            .join(
                AggregateBangumiSubjectRow,
                AggregateBangumiSubjectRow.aggregate_id == TorrentRow.aggregate_id,
            )
            .where(
                AggregateBangumiSubjectRow.subject_id
                == BangumiUserCollectionRow.subject_id
            )
            .exists()
        )
        state_predicates = {
            BangumiCollectionLocalState.UNMAPPED: ~mapping_exists,
            BangumiCollectionLocalState.EMPTY: mapping_exists & ~torrent_exists,
            BangumiCollectionLocalState.WITH_TORRENTS: torrent_exists,
        }
        coverage_rows = list(
            self.session.execute(
                select(BangumiUserCollectionRow, BangumiSubjectRow)
                .join(
                    BangumiSubjectRow,
                    BangumiSubjectRow.subject_id == BangumiUserCollectionRow.subject_id,
                )
                .where(
                    BangumiUserCollectionRow.username == username,
                    BangumiUserCollectionRow.removed_at.is_(None),
                    BangumiUserCollectionRow.collection_type.in_(
                        int(collection_type) for collection_type in collection_types
                    ),
                    or_(
                        *(state_predicates[local_state] for local_state in local_states)
                    ),
                )
                .order_by(BangumiUserCollectionRow.subject_id)
            )
        )
        subject_ids = [collection.subject_id for collection, _ in coverage_rows]
        aggregates_by_subject: dict[int, list[BangumiCollectionAggregateCoverage]] = {
            subject_id: [] for subject_id in subject_ids
        }
        if subject_ids:
            aggregate_rows = self.session.execute(
                select(
                    AggregateBangumiSubjectRow.subject_id,
                    AggregateRow.short_name,
                    func.count(TorrentRow.hash),
                )
                .join(
                    AggregateRow,
                    AggregateRow.id == AggregateBangumiSubjectRow.aggregate_id,
                )
                .outerjoin(TorrentRow, TorrentRow.aggregate_id == AggregateRow.id)
                .where(AggregateBangumiSubjectRow.subject_id.in_(subject_ids))
                .group_by(
                    AggregateBangumiSubjectRow.subject_id,
                    AggregateRow.id,
                    AggregateRow.short_name,
                )
                .order_by(
                    AggregateBangumiSubjectRow.subject_id,
                    AggregateRow.short_name,
                )
            )
            for subject_id, short_name, torrent_count in aggregate_rows:
                aggregates_by_subject[subject_id].append(
                    BangumiCollectionAggregateCoverage(
                        short_name=short_name,
                        torrent_count=torrent_count,
                    )
                )

        results = []
        for collection, subject_row in coverage_rows:
            aggregates = aggregates_by_subject[collection.subject_id]
            torrent_count = sum(aggregate.torrent_count for aggregate in aggregates)
            if not aggregates:
                local_state = BangumiCollectionLocalState.UNMAPPED
            elif torrent_count == 0:
                local_state = BangumiCollectionLocalState.EMPTY
            else:
                local_state = BangumiCollectionLocalState.WITH_TORRENTS
            results.append(
                BangumiCollectionSubjectCoverage(
                    subject=bangumi_subject_from_row(subject_row),
                    collection_type=BangumiCollectionType(collection.collection_type),
                    local_state=local_state,
                    aggregates=aggregates,
                    torrent_count=torrent_count,
                )
            )
        return results

    def synchronize(
        self,
        username: str,
        collections: list[BangumiRemoteCollection],
        synced_at: datetime,
    ) -> BangumiCollectionChanges:
        rows = {
            row.subject_id: row
            for row in self.session.scalars(
                select(BangumiUserCollectionRow).where(
                    BangumiUserCollectionRow.username == username
                )
            )
        }
        created = updated = removed = reactivated = unchanged = 0
        synced_at_text = synced_at.isoformat()
        seen_subject_ids: set[int] = set()

        for collection in collections:
            snapshot = collection.subject
            if snapshot is None:
                raise ValueError(
                    f"Bangumi subject {collection.subject_id} has no snapshot."
                )
            seen_subject_ids.add(collection.subject_id)
            upsert_subject_row(
                self.session,
                BangumiSubject(
                    subject_id=collection.subject_id,
                    last_updated_at=synced_at_text,
                    snapshot=snapshot,
                ),
            )
            row = rows.get(collection.subject_id)
            remote_updated_at = collection.updated_at.isoformat()
            if row is None:
                created += 1
                self.session.add(
                    BangumiUserCollectionRow(
                        username=username,
                        subject_id=collection.subject_id,
                        collection_type=int(collection.type),
                        remote_updated_at=remote_updated_at,
                        first_seen_at=synced_at_text,
                        last_seen_at=synced_at_text,
                        synced_at=synced_at_text,
                    )
                )
                continue

            if row.removed_at is not None:
                reactivated += 1
            elif (
                row.collection_type != int(collection.type)
                or row.remote_updated_at != remote_updated_at
            ):
                updated += 1
            else:
                unchanged += 1
            row.collection_type = int(collection.type)
            row.remote_updated_at = remote_updated_at
            row.last_seen_at = synced_at_text
            row.synced_at = synced_at_text
            row.removed_at = None

        for subject_id, row in rows.items():
            if subject_id in seen_subject_ids:
                continue
            row.synced_at = synced_at_text
            if row.removed_at is None:
                row.removed_at = synced_at_text
                removed += 1

        state = self.session.get(BangumiCollectionSyncStateRow, username)
        if state is None:
            self.session.add(
                BangumiCollectionSyncStateRow(
                    username=username,
                    last_successful_sync_at=synced_at_text,
                )
            )
        else:
            state.last_successful_sync_at = synced_at_text

        return BangumiCollectionChanges(
            fetched=len(collections),
            created=created,
            updated=updated,
            removed=removed,
            reactivated=reactivated,
            unchanged=unchanged,
        )


def collection_from_row(row: BangumiUserCollectionRow) -> BangumiUserCollection:
    return BangumiUserCollection(
        username=row.username,
        subject_id=row.subject_id,
        collection_type=BangumiCollectionType(row.collection_type),
        remote_updated_at=datetime.fromisoformat(row.remote_updated_at),
        first_seen_at=datetime.fromisoformat(row.first_seen_at),
        last_seen_at=datetime.fromisoformat(row.last_seen_at),
        synced_at=datetime.fromisoformat(row.synced_at),
        removed_at=(
            datetime.fromisoformat(row.removed_at)
            if row.removed_at is not None
            else None
        ),
    )
