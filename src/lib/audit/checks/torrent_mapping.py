from __future__ import annotations

from typing import TYPE_CHECKING, cast

from lib.audit.context import AuditContext
from lib.models.audit import AuditFinding, AuditSeverity
from lib.models.qbittorrent import QbittorrentTorrent

if TYPE_CHECKING:
    from pydantic import JsonValue


class TorrentMappingAuditor:
    name = "torrent_mapping"

    def audit(self, context: AuditContext) -> list[AuditFinding]:
        qbit_torrents = context.get_qbittorrent_torrents()
        qbit_by_hash = {torrent.hash: torrent for torrent in qbit_torrents}
        hash_locations: dict[str, list[str]] = {}
        for aggregate in context.get_aggregates():
            for torrent in aggregate.iter_torrents():
                hash_locations.setdefault(torrent.hash, []).append(aggregate.short_name)

        findings: list[AuditFinding] = []
        for torrent_hash, locations in hash_locations.items():
            if len(locations) > 1:
                findings.append(
                    self._mapping_finding(
                        code="torrent.duplicate_mapping",
                        severity=AuditSeverity.ERROR,
                        message="Torrent is mapped to multiple aggregates.",
                        torrent_hash=torrent_hash,
                        aggregates=locations,
                    )
                )

            torrent = qbit_by_hash.get(torrent_hash)
            if torrent is None:
                findings.append(
                    self._mapping_finding(
                        code="torrent.tracked_missing",
                        severity=AuditSeverity.WARNING,
                        message="Tracked torrent is missing from qBittorrent.",
                        torrent_hash=torrent_hash,
                        aggregates=locations,
                    )
                )
            else:
                findings.append(self._tracked_finding(torrent, locations))

        existing_hashes = set(hash_locations)
        categories_lower = [category.lower() for category in context.categories]
        for torrent in qbit_torrents:
            if (
                torrent.category.lower() in categories_lower
                and torrent.hash not in existing_hashes
            ):
                findings.append(
                    AuditFinding(
                        auditor=self.name,
                        code="torrent.unmapped",
                        severity=AuditSeverity.WARNING,
                        message="qBittorrent torrent is not mapped to an aggregate.",
                        torrent_hash=torrent.hash,
                        path=torrent.save_path or None,
                        metadata={
                            "torrent_name": torrent.name,
                            "category": torrent.category,
                        },
                    )
                )
        return findings

    def _tracked_finding(
        self,
        torrent: QbittorrentTorrent,
        aggregates: list[str],
    ) -> AuditFinding:
        return self._mapping_finding(
            code="torrent.tracked_found",
            severity=AuditSeverity.INFO,
            message="Tracked torrent is present in qBittorrent.",
            torrent_hash=torrent.hash,
            aggregates=aggregates,
            path=torrent.save_path or None,
            torrent_name=torrent.name,
            category=torrent.category,
        )

    def _mapping_finding(
        self,
        *,
        code: str,
        severity: AuditSeverity,
        message: str,
        torrent_hash: str,
        aggregates: list[str],
        path: str | None = None,
        torrent_name: str | None = None,
        category: str | None = None,
    ) -> AuditFinding:
        metadata: dict[str, JsonValue] = {"aggregates": cast("JsonValue", aggregates)}
        if torrent_name is not None:
            metadata["torrent_name"] = torrent_name
        if category is not None:
            metadata["category"] = category
        return AuditFinding(
            auditor=self.name,
            code=code,
            severity=severity,
            message=message,
            aggregate_short_name=aggregates[0] if len(aggregates) == 1 else None,
            torrent_hash=torrent_hash,
            path=path,
            metadata=metadata,
        )
