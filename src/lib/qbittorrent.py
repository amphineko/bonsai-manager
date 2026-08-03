import requests
from pydantic import TypeAdapter

from config import QbittorrentConfig, load_config
from lib.models.qbittorrent import QbittorrentTorrent, QbittorrentTorrentFile


class QbittorrentClient:
    def __init__(self, config: QbittorrentConfig | None = None):
        self.config = config or load_config().qbittorrent
        self.base_url = self.config.base_url
        self.session = requests.Session()

    def close(self) -> None:
        self.session.close()

    def login(self) -> None:
        auth_url = f"{self.base_url}/auth/login"
        resp = self.session.post(
            auth_url,
            data={
                "username": self.config.username,
                "password": self.config.password,
            },
        )
        resp.raise_for_status()

    def get_all_torrents(self) -> list[QbittorrentTorrent]:
        info_url = f"{self.base_url}/torrents/info"
        resp = self.session.get(info_url)
        resp.raise_for_status()
        return TypeAdapter(list[QbittorrentTorrent]).validate_python(resp.json())

    def get_torrent_info(self, torrent_hash: str) -> QbittorrentTorrent | None:
        torrents = self.get_torrents_info([torrent_hash])
        return torrents[0] if torrents else None

    def get_torrents_info(
        self,
        torrent_hashes: list[str],
    ) -> list[QbittorrentTorrent]:
        if not torrent_hashes:
            return []
        info_url = f"{self.base_url}/torrents/info"
        resp = self.session.get(
            info_url,
            params={"hashes": "|".join(torrent_hashes)},
        )
        resp.raise_for_status()
        return TypeAdapter(list[QbittorrentTorrent]).validate_python(resp.json())

    def get_torrent_files(self, torrent_hash: str) -> list[QbittorrentTorrentFile]:
        files_url = f"{self.base_url}/torrents/files"
        resp = self.session.get(files_url, params={"hash": torrent_hash})
        resp.raise_for_status()
        return TypeAdapter(list[QbittorrentTorrentFile]).validate_python(resp.json())
