from __future__ import annotations

from typing import TYPE_CHECKING

from lib.models.aggregates import Torrent

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from lib.models.aggregates import Aggregate
    from lib.qbittorrent import QbittorrentClient
    from lib.sql.repositories import SqliteAggregateRepository


class AggregateTorrentService:
    def __init__(
        self,
        repository: SqliteAggregateRepository,
        qbit: QbittorrentClient,
    ):
        self.repository = repository
        self.qbit = qbit

    def get_repository(
        self,
        *,
        write: bool,
    ) -> AbstractContextManager[SqliteAggregateRepository]:
        return self.repository.get_repository(write=write)

    def validate_torrent_hash_args(self, torrent_hashes: list[str]) -> None:
        if len(set(torrent_hashes)) != len(torrent_hashes):
            raise ValueError("Torrent hashes contain duplicates.")

    def validate_new_torrent_hashes_in_repository(
        self,
        repo: SqliteAggregateRepository,
        torrent_hashes: list[str],
    ) -> None:
        for torrent_hash in torrent_hashes:
            existing_entry = repo.get_by_torrent_hash(torrent_hash)
            if existing_entry is not None:
                raise ValueError(
                    f"Torrent hash already exists in '{existing_entry.short_name}'."
                )

    def validate_torrent_hashes_in_qbittorrent(self, torrent_hashes: list[str]) -> None:
        if torrent_hashes:
            self.qbit.login()

        for torrent_hash in torrent_hashes:
            info = self.qbit.get_torrent_info(torrent_hash)
            if not info:
                raise ValueError(
                    f"Torrent with hash '{torrent_hash}' not found in qBittorrent."
                )

    def build_torrents(self, torrent_hashes: list[str]) -> list[Torrent]:
        return [Torrent(hash=torrent_hash) for torrent_hash in torrent_hashes]

    def update_aggregate_torrents(
        self,
        short_name: str,
        add_hashes: list[str] | None = None,
        remove_hashes: list[str] | None = None,
    ) -> list[str]:
        add_hashes = add_hashes or []
        remove_hashes = remove_hashes or []
        try:
            self.validate_torrent_hash_args(add_hashes)
        except ValueError as exc:
            raise ValueError("Torrent hashes to add contain duplicates.") from exc
        try:
            self.validate_torrent_hash_args(remove_hashes)
        except ValueError as exc:
            raise ValueError("Torrent hashes to remove contain duplicates.") from exc
        if set(add_hashes) & set(remove_hashes):
            raise ValueError(
                "Cannot add and remove the same torrent hash in one update."
            )

        with self.get_repository(write=False) as repo:
            target_entry = repo.get_by_short_name(short_name)
            if not target_entry:
                raise ValueError(f"Anime '{short_name}' not found.")
            self._validate_torrent_removals(short_name, target_entry, remove_hashes)
            self.validate_new_torrent_hashes_in_repository(repo, add_hashes)

        self.validate_torrent_hashes_in_qbittorrent(add_hashes)

        with self.get_repository(write=True) as repo:
            target_entry = repo.get_by_short_name(short_name)
            if not target_entry:
                raise ValueError(f"Anime '{short_name}' not found.")

            self._validate_torrent_removals(short_name, target_entry, remove_hashes)
            self.validate_new_torrent_hashes_in_repository(repo, add_hashes)
            target_entry.torrents = [
                torrent
                for torrent in target_entry.torrents
                if torrent.hash not in set(remove_hashes)
            ]
            target_entry.torrents.extend(
                Torrent(hash=torrent_hash) for torrent_hash in add_hashes
            )
            repo.update_torrents(short_name, target_entry.torrents)
            return [torrent.hash for torrent in target_entry.torrents]

    def _validate_torrent_removals(
        self,
        short_name: str,
        target_entry: Aggregate,
        remove_hashes: list[str],
    ) -> None:
        current_hashes = [torrent.hash for torrent in target_entry.torrents]
        current_hash_set = set(current_hashes)
        missing_hashes = [
            torrent_hash
            for torrent_hash in remove_hashes
            if torrent_hash not in current_hash_set
        ]
        if missing_hashes:
            raise ValueError(
                f"Torrent hash not found in '{short_name}': {', '.join(missing_hashes)}"
            )
