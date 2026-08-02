from __future__ import annotations

from dataclasses import dataclass, field

from lib.audit.protocols import AuditQbittorrentClient
from lib.models.aggregates import Aggregate
from lib.models.qbittorrent import QbittorrentTorrent, QbittorrentTorrentFile
from lib.sql.repositories import AggregateRepository


@dataclass
class AuditContext:
    repository: AggregateRepository
    qbit: AuditQbittorrentClient
    categories: tuple[str, ...]
    _aggregates: tuple[Aggregate, ...] | None = field(default=None, init=False)
    _qbit_torrents: tuple[QbittorrentTorrent, ...] | None = field(
        default=None,
        init=False,
    )
    _torrent_files: dict[str, tuple[QbittorrentTorrentFile, ...]] = field(
        default_factory=dict,
        init=False,
    )
    _qbit_authenticated: bool = field(default=False, init=False)

    def get_aggregates(self) -> tuple[Aggregate, ...]:
        if self._aggregates is None:
            with self.repository.get_repository(write=False) as repo:
                self._aggregates = tuple(repo.list_all())
        return self._aggregates

    def get_qbittorrent_torrents(self) -> tuple[QbittorrentTorrent, ...]:
        if self._qbit_torrents is None:
            self._login_qbittorrent()
            self._qbit_torrents = tuple(self.qbit.get_all_torrents())
        return self._qbit_torrents

    def get_torrent_files(
        self,
        torrent_hash: str,
    ) -> tuple[QbittorrentTorrentFile, ...]:
        if torrent_hash not in self._torrent_files:
            self._login_qbittorrent()
            self._torrent_files[torrent_hash] = tuple(
                self.qbit.get_torrent_files(torrent_hash)
            )
        return self._torrent_files[torrent_hash]

    def _login_qbittorrent(self) -> None:
        if self._qbit_authenticated:
            return
        self.qbit.login()
        self._qbit_authenticated = True
