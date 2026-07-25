from __future__ import annotations

from typing import TYPE_CHECKING

from lib.models.aggregates import Aggregate

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from lib.services.bangumi import AggregateBangumiService
    from lib.services.torrents import AggregateTorrentService
    from lib.sql.repositories import SqliteAggregateRepository


class AggregateCreationService:
    def __init__(
        self,
        repository: SqliteAggregateRepository,
        torrents: AggregateTorrentService,
        bangumi: AggregateBangumiService,
        aggregate_category: str,
    ):
        self.repository = repository
        self.torrents = torrents
        self.bangumi = bangumi
        self.aggregate_category = aggregate_category

    def get_repository(
        self,
        *,
        write: bool,
    ) -> AbstractContextManager[SqliteAggregateRepository]:
        return self.repository.get_repository(write=write)

    def add_aggregate(
        self,
        short_name: str,
        bangumi_subject_id: int | None = None,
        torrent_hashes: list[str] | None = None,
    ) -> Aggregate:
        torrent_hashes = torrent_hashes or []
        subject_ids = [] if bangumi_subject_id is None else [bangumi_subject_id]
        self.torrents.validate_torrent_hash_args(torrent_hashes)
        with self.get_repository(write=False) as repo:
            if repo.get_by_short_name(short_name) is not None:
                raise ValueError(f"Aggregate '{short_name}' already exists.")
            self.torrents.validate_new_torrent_hashes_in_repository(
                repo, torrent_hashes
            )

        self.torrents.validate_torrent_hashes_in_qbittorrent(torrent_hashes)
        bangumi_subjects = self.bangumi.build_subjects(subject_ids)

        with self.get_repository(write=True) as repo:
            if repo.get_by_short_name(short_name) is not None:
                raise ValueError(f"Aggregate '{short_name}' already exists.")

            self.torrents.validate_new_torrent_hashes_in_repository(
                repo, torrent_hashes
            )

            new_entry = Aggregate(
                short_name=short_name,
                category=self.aggregate_category,
                bangumi_subjects=bangumi_subjects,
                torrents=self.torrents.build_torrents(torrent_hashes),
            )
            repo.add(new_entry)
            return new_entry
