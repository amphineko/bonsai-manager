import os
from contextlib import AbstractContextManager
from typing import List

from lib.models.aggregates import Aggregate, Torrent
from lib.qbittorrent import QbittorrentClient
from lib.sql.repositories import SqliteAggregateRepository


def get_torrent_display_path(save_path: str, files: List[str]) -> str:
    if not files:
        return save_path
    top_levels = set()
    for f in files:
        parts = f.split("/")
        if parts:
            top_levels.add(parts[0])
    if len(top_levels) == 1:
        return os.path.join(save_path, list(top_levels)[0])
    else:
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
        filter_short_name: List[str] | None = None,
        filter_torrent_hashes: List[str] | None = None,
        filter_bangumi_subject_name: List[str] | None = None,
        filter_bangumi_subject_cn_name: List[str] | None = None,
    ) -> List[Aggregate]:
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

    def get_torrent_display_path(self, torrent: Torrent) -> str:
        self.qbit.login()
        info = self.qbit.get_torrent_info(torrent.hash)
        if not info:
            return torrent.hash
        files = self.qbit.get_torrent_files(torrent.hash)
        return get_torrent_display_path(info.save_path, [file.name for file in files])

    def remove_aggregate(self, short_name: str) -> Aggregate:
        with self.get_repository(write=True) as repo:
            removed_entry = repo.remove_by_short_name(short_name)
            if removed_entry is None:
                raise ValueError(f"Aggregate '{short_name}' not found.")
            return removed_entry
