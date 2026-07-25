from __future__ import annotations

from typing import TYPE_CHECKING

from lib.models.qbittorrent import (
    TorrentMappingAudit,
    TorrentMappingLocation,
    TrackedTorrentMapping,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from lib.qbittorrent import QbittorrentClient
    from lib.sql.repositories import SqliteAggregateRepository


class AggregateAuditService:
    def __init__(
        self,
        repository: SqliteAggregateRepository,
        qbit: QbittorrentClient,
        audit_categories: tuple[str, ...],
    ):
        self.repository = repository
        self.qbit = qbit
        self.audit_categories = audit_categories

    def get_repository(
        self,
        *,
        write: bool,
    ) -> AbstractContextManager[SqliteAggregateRepository]:
        return self.repository.get_repository(write=write)

    def audit_torrent_mapping(
        self,
        categories: list[str] | None = None,
    ) -> TorrentMappingAudit:
        categories = categories or list(self.audit_categories)
        with self.get_repository(write=False) as repo:
            entries = repo.list_all()
        self.qbit.login()
        qbit_torrents = self.qbit.get_all_torrents()

        qbit_by_hash = {torrent.hash: torrent for torrent in qbit_torrents}
        hash_locations = {}
        for entry in entries:
            for torrent in entry.torrents:
                hash_locations.setdefault(torrent.hash, []).append(entry.short_name)

        tracked_found: list[TrackedTorrentMapping] = []
        tracked_missing: list[TorrentMappingLocation] = []
        duplicates: list[TorrentMappingLocation] = []

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
