from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lib.models.aggregates import Aggregate, Torrent
from lib.models.qbittorrent import QbittorrentTorrent
from lib.qbittorrent import QbittorrentClient
from lib.sql.repositories import SqliteAggregateRepository

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


def get_torrent_display_path(save_path: str, files: list[str]) -> str:
    if not files:
        return save_path
    top_levels = set()
    for f in files:
        parts = f.split("/")
        if parts:
            top_levels.add(parts[0])
    if len(top_levels) == 1:
        return str(Path(save_path) / next(iter(top_levels)))
    return save_path


class AggregateQueryService:
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

    def list_aggregates(
        self,
        filter_short_name: list[str] | None = None,
        filter_torrent_hashes: list[str] | None = None,
        filter_bangumi_subject_name: list[str] | None = None,
        filter_bangumi_subject_cn_name: list[str] | None = None,
    ) -> list[Aggregate]:
        filter_short_name = filter_short_name or []
        filter_torrent_hashes = filter_torrent_hashes or []
        filter_bangumi_subject_name = filter_bangumi_subject_name or []
        filter_bangumi_subject_cn_name = filter_bangumi_subject_cn_name or []

        with self.get_repository(write=False) as repo:
            if (
                not filter_short_name
                and not filter_torrent_hashes
                and not filter_bangumi_subject_name
                and not filter_bangumi_subject_cn_name
            ):
                return repo.list_all()

            return repo.find(
                filter_short_name,
                filter_torrent_hashes,
                filter_bangumi_subject_name,
                filter_bangumi_subject_cn_name,
            )

    def count_aggregates(self) -> int:
        with self.get_repository(write=False) as repo:
            return repo.count_aggregates()

    def get_by_short_names(self, short_names: list[str]) -> list[Aggregate]:
        with self.get_repository(write=False) as repo:
            return repo.get_by_short_names(short_names)

    def get_torrent_display_path(self, torrent: Torrent) -> str:
        self.qbit.login()
        info = self.qbit.get_torrent_info(torrent.hash)
        if not info:
            return torrent.hash
        files = self.qbit.get_torrent_files(torrent.hash)
        return get_torrent_display_path(info.save_path, [file.name for file in files])

    def get_torrent_info(
        self,
        torrent_hashes: list[str],
    ) -> list[QbittorrentTorrent]:
        normalized_hashes = [torrent_hash.casefold() for torrent_hash in torrent_hashes]
        if len(set(normalized_hashes)) != len(normalized_hashes):
            raise ValueError("Torrent hashes contain duplicates.")
        if not torrent_hashes:
            return []

        self.qbit.login()
        torrents = self.qbit.get_torrents_info(torrent_hashes)
        torrents_by_hash = {torrent.hash.casefold(): torrent for torrent in torrents}
        return [
            torrents_by_hash[torrent_hash]
            for torrent_hash in normalized_hashes
            if torrent_hash in torrents_by_hash
        ]

    def remove_aggregate(self, short_name: str) -> Aggregate:
        with self.get_repository(write=True) as repo:
            removed_entry = repo.remove_by_short_name(short_name)
            if removed_entry is None:
                raise ValueError(f"Aggregate '{short_name}' not found.")
            return removed_entry
