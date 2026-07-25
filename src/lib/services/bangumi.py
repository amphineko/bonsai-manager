from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lib.models.bangumi import BangumiSubject

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from lib.bangumi import BangumiClient
    from lib.models.aggregates import Aggregate
    from lib.sql.repositories import SqliteAggregateRepository


class AggregateBangumiService:
    def __init__(
        self,
        repository: SqliteAggregateRepository,
        bangumi: BangumiClient,
    ):
        self.repository = repository
        self.bangumi = bangumi

    def get_repository(
        self,
        *,
        write: bool,
    ) -> AbstractContextManager[SqliteAggregateRepository]:
        return self.repository.get_repository(write=write)

    def build_subjects(self, subject_ids: list[int]) -> list[BangumiSubject]:
        now = datetime.now(UTC).isoformat()
        return [
            BangumiSubject(
                subject_id=subject_id,
                last_updated_at=now,
                snapshot=self.bangumi.get_subject_snapshot(subject_id),
            )
            for subject_id in subject_ids
        ]

    def update_aggregate_bangumi_subjects(
        self,
        short_name: str,
        add_subject_ids: list[int] | None = None,
        remove_subject_ids: list[int] | None = None,
    ) -> list[int]:
        add_subject_ids = add_subject_ids or []
        remove_subject_ids = remove_subject_ids or []
        if len(set(add_subject_ids)) != len(add_subject_ids):
            raise ValueError("Bangumi subject IDs to add contain duplicates.")
        if len(set(remove_subject_ids)) != len(remove_subject_ids):
            raise ValueError("Bangumi subject IDs to remove contain duplicates.")
        if set(add_subject_ids) & set(remove_subject_ids):
            raise ValueError(
                "Cannot add and remove the same Bangumi subject ID in one update."
            )

        with self.get_repository(write=False) as repo:
            target_entry = repo.get_by_short_name(short_name)
            if not target_entry:
                raise ValueError(f"Anime '{short_name}' not found.")

            self.validate_bangumi_subject_update(
                short_name,
                target_entry,
                add_subject_ids,
                remove_subject_ids,
            )

        added_subjects = self.build_subjects(add_subject_ids)

        with self.get_repository(write=True) as repo:
            target_entry = repo.get_by_short_name(short_name)
            if not target_entry:
                raise ValueError(f"Anime '{short_name}' not found.")

            self.validate_bangumi_subject_update(
                short_name,
                target_entry,
                add_subject_ids,
                remove_subject_ids,
            )

            target_entry.bangumi_subjects = [
                subject
                for subject in target_entry.bangumi_subjects
                if subject.subject_id not in set(remove_subject_ids)
            ]
            target_entry.bangumi_subjects.extend(added_subjects)
            repo.update_bangumi_subjects(
                short_name,
                target_entry.bangumi_subjects,
            )
            return [subject.subject_id for subject in target_entry.bangumi_subjects]

    def validate_bangumi_subject_update(
        self,
        short_name: str,
        target_entry: Aggregate,
        add_subject_ids: list[int],
        remove_subject_ids: list[int],
    ) -> None:
        current_subject_ids = [
            subject.subject_id for subject in target_entry.bangumi_subjects
        ]
        current_subject_id_set = set(current_subject_ids)
        existing_subject_ids = [
            subject_id
            for subject_id in add_subject_ids
            if subject_id in current_subject_id_set
        ]
        if existing_subject_ids:
            raise ValueError(
                f"Bangumi subject already exists in '{short_name}': "
                f"{', '.join(str(subject_id) for subject_id in existing_subject_ids)}"
            )

        missing_subject_ids = [
            subject_id
            for subject_id in remove_subject_ids
            if subject_id not in current_subject_id_set
        ]
        if missing_subject_ids:
            raise ValueError(
                f"Bangumi subject not found in '{short_name}': "
                f"{', '.join(str(subject_id) for subject_id in missing_subject_ids)}"
            )
