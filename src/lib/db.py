import json
import os
from pathlib import Path
from fnmatch import fnmatch
from datetime import datetime
from typing import List, Dict
from pydantic import TypeAdapter

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


class DBManager:
    def __init__(
        self,
        config: Config | str | Path | None = None,
        db_path: str | Path | None = None,
    ):
        if isinstance(config, str | Path):
            db_path = config
            config = None

        self.config = config or load_config()
        self.db_path = Path(db_path) if db_path is not None else self.config.db_path
        self.qbit = QbittorrentClient(self.config.qbittorrent)
        self.bangumi = BangumiClient(self.config.bangumi)

    def load_db(self) -> List[Aggregate]:
        if not self.db_path.exists():
            return []
        with self.db_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return TypeAdapter(List[Aggregate]).validate_python(data)

    def save_db(self, entries: List[Aggregate]) -> None:
        with self.db_path.open("w", encoding="utf-8") as f:
            data = [entry.model_dump() for entry in entries]
            json.dump(data, f, ensure_ascii=False, indent=2)

    def touch_entry(self, entry: Aggregate, timestamp: str) -> None:
        for subject in entry.bangumi_subjects:
            subject.last_updated_at = timestamp

    def validate_new_torrent_hashes(
        self, entries: List[Aggregate], torrent_hashes: List[str]
    ) -> None:
        if len(set(torrent_hashes)) != len(torrent_hashes):
            raise ValueError("Torrent hashes contain duplicates.")

        for torrent_hash in torrent_hashes:
            for entry in entries:
                if any(t.hash == torrent_hash for t in entry.torrents):
                    raise ValueError(
                        f"Torrent hash already exists in '{entry.short_name}'."
                    )

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
        entries = self.load_db()
        if any(e.short_name == short_name for e in entries):
            raise ValueError(f"Aggregate '{short_name}' already exists.")

        torrent_hashes = torrent_hashes or []
        self.validate_new_torrent_hashes(entries, torrent_hashes)

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

        new_entry = Aggregate(
            short_name=short_name,
            category=self.config.aggregate_category,
            bangumi_subjects=bangumi_subjects,
            torrents=[Torrent(hash=torrent_hash) for torrent_hash in torrent_hashes],
        )
        entries.append(new_entry)
        self.save_db(entries)
        return new_entry

    def remove_aggregate(self, short_name: str) -> Aggregate:
        entries = self.load_db()
        target_index = next(
            (
                index
                for index, entry in enumerate(entries)
                if entry.short_name == short_name
            ),
            None,
        )
        if target_index is None:
            raise ValueError(f"Aggregate '{short_name}' not found.")

        removed_entry = entries.pop(target_index)
        self.save_db(entries)
        return removed_entry

    def add_torrent(self, short_name: str, torrent_hash: str) -> Torrent:
        hashes = self.update_aggregate_torrents(short_name, add_hashes=[torrent_hash])
        return Torrent(hash=hashes[-1])

    def update_aggregate_torrents(
        self,
        short_name: str,
        add_hashes: List[str] | None = None,
        remove_hashes: List[str] | None = None,
    ) -> List[str]:
        entries = self.load_db()
        target_entry = next((e for e in entries if e.short_name == short_name), None)
        if not target_entry:
            raise ValueError(f"Anime '{short_name}' not found.")

        add_hashes = add_hashes or []
        remove_hashes = remove_hashes or []
        if len(set(add_hashes)) != len(add_hashes):
            raise ValueError("Torrent hashes to add contain duplicates.")
        if len(set(remove_hashes)) != len(remove_hashes):
            raise ValueError("Torrent hashes to remove contain duplicates.")
        if set(add_hashes) & set(remove_hashes):
            raise ValueError(
                "Cannot add and remove the same torrent hash in one update."
            )

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

        self.validate_new_torrent_hashes(entries, add_hashes)
        target_entry.torrents = [
            torrent
            for torrent in target_entry.torrents
            if torrent.hash not in set(remove_hashes)
        ]
        target_entry.torrents.extend(
            Torrent(hash=torrent_hash) for torrent_hash in add_hashes
        )
        self.touch_entry(target_entry, datetime.now().isoformat())
        self.save_db(entries)
        return [torrent.hash for torrent in target_entry.torrents]

    def update_aggregate_bangumi_subjects(
        self,
        short_name: str,
        add_subject_ids: List[int] | None = None,
        remove_subject_ids: List[int] | None = None,
    ) -> List[int]:
        entries = self.load_db()
        target_entry = next((e for e in entries if e.short_name == short_name), None)
        if not target_entry:
            raise ValueError(f"Anime '{short_name}' not found.")

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

        now = datetime.now().isoformat()
        target_entry.bangumi_subjects = [
            subject
            for subject in target_entry.bangumi_subjects
            if subject.subject_id not in set(remove_subject_ids)
        ]
        for subject_id in add_subject_ids:
            target_entry.bangumi_subjects.append(
                BangumiSubject(
                    subject_id=subject_id,
                    last_updated_at=now,
                    snapshot=self.bangumi.get_subject_snapshot(subject_id),
                )
            )
        self.touch_entry(target_entry, now)
        self.save_db(entries)
        return [subject.subject_id for subject in target_entry.bangumi_subjects]

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
        if (
            not filter_short_name
            and not filter_torrent_hashes
            and not filter_bangumi_subject_name
            and not filter_bangumi_subject_cn_name
        ):
            raise ValueError("At least one filter argument is required.")

        filter_torrent_hash_set = set(filter_torrent_hashes)
        matches = []
        for entry in self.load_db():
            short_name_matches = any(
                fnmatch(entry.short_name, pattern) for pattern in filter_short_name
            )
            torrent_hash_matches = any(
                torrent.hash in filter_torrent_hash_set for torrent in entry.torrents
            )
            bangumi_subject_name_matches = any(
                subject.snapshot
                and any(
                    fnmatch(subject.snapshot.name, pattern)
                    for pattern in filter_bangumi_subject_name
                )
                for subject in entry.bangumi_subjects
            )
            bangumi_subject_cn_name_matches = any(
                subject.snapshot
                and any(
                    fnmatch(subject.snapshot.name_cn, pattern)
                    for pattern in filter_bangumi_subject_cn_name
                )
                for subject in entry.bangumi_subjects
            )
            if (
                short_name_matches
                or torrent_hash_matches
                or bangumi_subject_name_matches
                or bangumi_subject_cn_name_matches
            ):
                matches.append(entry)
        return matches

    def get_torrent_display_path(self, torrent: Torrent) -> str:
        self.qbit.login()
        info = self.qbit.get_torrent_info(torrent.hash)
        if not info:
            return torrent.hash
        files = self.qbit.get_torrent_files(torrent.hash)
        return get_torrent_display_path(info.save_path, [file.name for file in files])

    def move_torrent(self, torrent_hash: str, target_short_name: str) -> str:
        entries = self.load_db()

        source_entry = None
        torrent_to_move = None
        for entry in entries:
            for i, t in enumerate(entry.torrents):
                if t.hash == torrent_hash:
                    source_entry = entry
                    torrent_to_move = entry.torrents.pop(i)
                    break
            if source_entry:
                break

        if not source_entry:
            raise ValueError(
                f"Torrent with hash '{torrent_hash}' not found in database."
            )
        if torrent_to_move is None:
            raise ValueError(
                f"Torrent with hash '{torrent_hash}' not found in database."
            )

        target_entry = next(
            (e for e in entries if e.short_name == target_short_name), None
        )
        if not target_entry:
            source_entry.torrents.append(torrent_to_move)
            raise ValueError(f"Target anime '{target_short_name}' not found.")

        target_entry.torrents.append(torrent_to_move)

        now = datetime.now().isoformat()
        self.touch_entry(source_entry, now)
        self.touch_entry(target_entry, now)

        self.save_db(entries)
        return source_entry.short_name

    def audit_torrent_mapping(
        self,
        categories: List[str] | None = None,
    ) -> TorrentMappingAudit:
        categories = categories or list(self.config.audit_categories)
        entries = self.load_db()
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
        entries = self.load_db()
        updated_count = 0
        for entry in entries:
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
        self.save_db(entries)
        return updated_count

    def audit_bangumi_wish(
        self,
        username: str,
        collection_type: int = 1,
        subject_type: int = 2,
    ):
        """Audit Bangumi Wish vs local catalog.

        collection_type: 1=Wish, 3=Doing etc. Default 1 = 想看
        Returns BangumiWishAudit with missing = Wish but not in local DB.
        """
        from lib.models.bangumi import BangumiWishAudit, BangumiWishSubject

        # Fetch all wish collections
        wish_items = self.bangumi.get_all_user_collections(
            username, subject_type=subject_type, collection_type=collection_type
        )

        # Local subject ids
        local_subject_ids: set[int] = set()
        for entry in self.load_db():
            for subj in entry.bangumi_subjects:
                local_subject_ids.add(subj.subject_id)

        missing: list[BangumiWishSubject] = []
        existing: list[BangumiWishSubject] = []

        for item in wish_items:
            subject = item.get("subject", {}) if isinstance(item, dict) else {}
            sid = subject.get("id")
            if sid is None:
                continue
            ws = BangumiWishSubject(
                subject_id=sid,
                name=subject.get("name", "") or "",
                name_cn=subject.get("name_cn", "") or "",
                subject_type=subject.get("type"),
                collection_type=item.get("type"),
            )
            if sid in local_subject_ids:
                existing.append(ws)
            else:
                missing.append(ws)

        return BangumiWishAudit(
            username=username,
            wish_total=len(wish_items),
            local_total=len(local_subject_ids),
            missing_count=len(missing),
            missing=missing,
            existing=existing,
        )
