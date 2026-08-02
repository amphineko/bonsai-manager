from pydantic import BaseModel


class QbittorrentTorrent(BaseModel):
    hash: str
    name: str = ""
    category: str = ""
    save_path: str = ""


class QbittorrentTorrentFile(BaseModel):
    name: str
    size: int = 0
    progress: float = 0
    priority: int = 0
    is_seed: bool = False
