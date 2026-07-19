import os
from contextlib import AbstractContextManager
from datetime import datetime
from typing import List, Dict

from config import Config, load_config
from lib.bangumi import BangumiClient
from lib.models.aggregates import Aggregate, Torrent
from lib.models.bangumi import BangumiSubject, BangumiSubjectSnapshot
from lib.models.qbittorrent import (
    TorrentMappingAudit,
    TorrentMappingLocation,
    TrackedTorrentMapping,
)
from lib.qbittorrent import QbittorrentClient
from lib.sql.repositories import (
    SqliteAggregateRepository,
)


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


class AggregateService:
    def __init__(
        self,
        config: Config | None = None,
        repository: SqliteAggregateRepository | None = None,
    ):
        self.config = config or load_config()
        self.repository = repository or SqliteAggregateRepository(
            self.config.database.path
        )
        self.qbit = QbittorrentClient(self.config.qbittorrent)
        self.bangumi = BangumiClient(self.config.bangumi)

    def get_repository(
        self,
        *,
        write: bool,
    ) -> AbstractContextManager[SqliteAggregateRepository]:
        return self.repository.get_repository(write=write)

    def _validate_torrent_hash_args(self, torrent_hashes: List[str]) -> None:
        if len(set(torrent_hashes)) != len(torrent_hashes):
            raise ValueError("Torrent hashes contain duplicates.")

    def _validate_new_torrent_hashes_in_repository(
        self,
        repo: SqliteAggregateRepository,
        torrent_hashes: List[str],
    ) -> None:
        for torrent_hash in torrent_hashes:
            existing_entry = repo.get_by_torrent_hash(torrent_hash)
            if existing_entry is not None:
                raise ValueError(
                    f"Torrent hash already exists in '{existing_entry.short_name}'."
                )

    def _validate_torrent_hashes_in_qbittorrent(self, torrent_hashes: List[str]) -> None:
        if torrent_hashes:
            self.qbit.login()

        for torrent_hash in torrent_hashes:
            info = self.qbit.get_torrent_info(torrent_hash)
            if not info:
                raise ValueError(
                    f"Torrent with hash '{torrent_hash}' not found in qBittorrent."
                )

    def add_aggregate(
        self,
        short_name: str,
        bangumi_subject_id: int | None = None,
        torrent_hashes: List[str] | None = None,
    ) -> Aggregate:
        torrent_hashes = torrent_hashes or []
        self._validate_torrent_hash_args(torrent_hashes)
        with self.get_repository(write=False) as repo:
            if repo.get_by_short_name(short_name) is not None:
                raise ValueError(f"Aggregate '{short_name}' already exists.")
            self._validate_new_torrent_hashes_in_repository(repo, torrent_hashes)

        self._validate_torrent_hashes_in_qbittorrent(torrent_hashes)
        bangumi_subjects = []
        if bangumi_subject_id is not None:
            snapshot = self.bangumi.get_subject_snapshot(bangumi_subject_id)
            bangumi_subjects.append(
                BangumiSubject(
                    subject_id=bangumi_subject_id,
                    last_updated_at=datetime.now().isoformat(),
                    snapshot=snapshot,
                )
            )

        with self.get_repository(write=True) as repo:
            if repo.get_by_short_name(short_name) is not None:
                raise ValueError(f"Aggregate '{short_name}' already exists.")

            self._validate_new_torrent_hashes_in_repository(repo, torrent_hashes)

            new_entry = Aggregate(
                short_name=short_name,
                category=self.config.aggregate_category,
                bangumi_subjects=bangumi_subjects,
                torrents=[
                    Torrent(hash=torrent_hash) for torrent_hash in torrent_hashes
                ],
            )
            repo.add(new_entry)
            return new_entry

    def remove_aggregate(self, short_name: str) -> Aggregate:
        with self.get_repository(write=True) as repo:
            removed_entry = repo.remove_by_short_name(short_name)
            if removed_entry is None:
                raise ValueError(f"Aggregate '{short_name}' not found.")
            return removed_entry

    def update_aggregate_torrents(
        self,
        short_name: str,
        add_hashes: List[str] | None = None,
        remove_hashes: List[str] | None = None,
    ) -> List[str]:
        add_hashes = add_hashes or []
        remove_hashes = remove_hashes or []
        try:
            self._validate_torrent_hash_args(add_hashes)
        except ValueError:
            raise ValueError("Torrent hashes to add contain duplicates.")
        try:
            self._validate_torrent_hash_args(remove_hashes)
        except ValueError:
            raise ValueError("Torrent hashes to remove contain duplicates.")
        if set(add_hashes) & set(remove_hashes):
            raise ValueError(
                "Cannot add and remove the same torrent hash in one update."
            )

        with self.get_repository(write=False) as repo:
            target_entry = repo.get_by_short_name(short_name)
            if not target_entry:
                raise ValueError(f"Anime '{short_name}' not found.")
            self._validate_torrent_removals(short_name, target_entry, remove_hashes)
            self._validate_new_torrent_hashes_in_repository(repo, add_hashes)

        self._validate_torrent_hashes_in_qbittorrent(add_hashes)

        with self.get_repository(write=True) as repo:
            target_entry = repo.get_by_short_name(short_name)
            if not target_entry:
                raise ValueError(f"Anime '{short_name}' not found.")

            self._validate_torrent_removals(short_name, target_entry, remove_hashes)
            self._validate_new_torrent_hashes_in_repository(repo, add_hashes)
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
        remove_hashes: List[str],
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
                f"Torrent hash not found in '{short_name}': "
                f"{', '.join(missing_hashes)}"
            )

    def update_aggregate_bangumi_subjects(
        self,
        short_name: str,
        add_subject_ids: List[int] | None = None,
        remove_subject_ids: List[int] | None = None,
    ) -> List[int]:
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

            self._validate_bangumi_subject_update(
                short_name,
                target_entry,
                add_subject_ids,
                remove_subject_ids,
            )

        added_subjects = []
        now = datetime.now().isoformat()
        for subject_id in add_subject_ids:
            added_subjects.append(
                BangumiSubject(
                    subject_id=subject_id,
                    last_updated_at=now,
                    snapshot=self.bangumi.get_subject_snapshot(subject_id),
                )
            )

        with self.get_repository(write=True) as repo:
            target_entry = repo.get_by_short_name(short_name)
            if not target_entry:
                raise ValueError(f"Anime '{short_name}' not found.")

            self._validate_bangumi_subject_update(
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

    def _validate_bangumi_subject_update(
        self,
        short_name: str,
        target_entry: Aggregate,
        add_subject_ids: List[int],
        remove_subject_ids: List[int],
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

    def move_torrent(self, torrent_hash: str, target_short_name: str) -> str:
        with self.get_repository(write=True) as repo:
            source_entry = repo.get_by_torrent_hash(torrent_hash)
            if not source_entry:
                raise ValueError(
                    f"Torrent with hash '{torrent_hash}' not found in database."
                )
            torrent_to_move = next(
                (
                    torrent
                    for torrent in source_entry.torrents
                    if torrent.hash == torrent_hash
                ),
                None,
            )
            if torrent_to_move is None:
                raise ValueError(
                    f"Torrent with hash '{torrent_hash}' not found in database."
                )
            source_entry.torrents = [
                torrent
                for torrent in source_entry.torrents
                if torrent.hash != torrent_hash
            ]

            if source_entry.short_name == target_short_name:
                target_entry = source_entry
            else:
                target_entry = repo.get_by_short_name(target_short_name)
                if not target_entry:
                    source_entry.torrents.append(torrent_to_move)
                    raise ValueError(f"Target anime '{target_short_name}' not found.")

            target_entry.torrents.append(torrent_to_move)

            repo.update_torrents(source_entry.short_name, source_entry.torrents)
            repo.update_torrents(target_entry.short_name, target_entry.torrents)
            return source_entry.short_name

    def audit_torrent_mapping(
        self,
        categories: List[str] | None = None,
    ) -> TorrentMappingAudit:
        categories = categories or list(self.config.audit_categories)
        with self.get_repository(write=False) as repo:
            entries = repo.list_all()
        self.qbit.login()
        qbit_torrents = self.qbit.get_all_torrents()

        qbit_by_hash = {torrent.hash: torrent for torrent in qbit_torrents}
        hash_locations = {}
        for entry in entries:
            for torrent in entry.torrents:
                hash_locations.setdefault(torrent.hash, []).append(entry.short_name)

        tracked_found: List[TrackedTorrentMapping] = []
        tracked_missing: List[TorrentMappingLocation] = []
        duplicates: List[TorrentMappingLocation] = []

        for torrent_hash, locations in hash_locations.items():
            if len(locations) > 1:
                duplicates.append(
                    TorrentMappingLocation(hash=torrent_hash, aggregates=locations)
                )
            if torrent_hash in qbit_by_hash:
                tracked_found.append(
                    TrackedTorrentMapping(
                        hash=torrent_hash,
                        aggregates=locations,
                        torrent=qbit_by_hash[torrent_hash],
                    )
                )
            else:
                tracked_missing.append(
                    TorrentMappingLocation(hash=torrent_hash, aggregates=locations)
                )

        existing_hashes = set(hash_locations)
        categories_lower = [c.lower() for c in categories]
        unmapped = []
        for torrent in qbit_torrents:
            torrent_category = torrent.category.lower()
            if (
                torrent_category in categories_lower
                and torrent.hash not in existing_hashes
            ):
                unmapped.append(torrent)

        return TorrentMappingAudit(
            tracked_found=tracked_found,
            tracked_missing=tracked_missing,
            unmapped=unmapped,
            duplicates=duplicates,
        )

    def sync_bangumi_types(self, subject_data: Dict[int, int]):
        """Update subject types from provided Bangumi mapping (fetched via MCP)."""
        with self.get_repository(write=True) as repo:
            entries = repo.list_all()
            updated_count = 0
            for entry in entries:
                entry_updated = False
                for subject in entry.bangumi_subjects:
                    if subject.subject_id in subject_data:
                        if subject.snapshot:
                            subject.snapshot.type = subject_data[subject.subject_id]
                        else:
                            subject.snapshot = BangumiSubjectSnapshot(
                                name="",
                                name_cn="",
                                type=subject_data[subject.subject_id],
                            )
                        updated_count += 1
                        entry_updated = True
                if entry_updated:
                    repo.update_bangumi_subjects(
                        entry.short_name,
                        entry.bangumi_subjects,
                    )
            return updated_count


DBManager = AggregateService
