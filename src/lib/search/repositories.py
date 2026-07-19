from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter

from config import SearchConfig
from lib.models.search import SearchIndex, SearchQueryCache


class SearchRepository(Protocol):
    def load_index(self) -> SearchIndex: ...

    def save_index(self, index: SearchIndex) -> None: ...

    def load_query_cache(self) -> SearchQueryCache: ...

    def save_query_cache(self, cache: SearchQueryCache) -> None: ...


class JsonSearchRepository:
    def __init__(self, config: SearchConfig):
        self.config = config

    def load_index(self) -> SearchIndex:
        if not self.config.index_path.exists():
            return SearchIndex(embedding_model=self.config.embedding_model)
        with self.config.index_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return TypeAdapter(SearchIndex).validate_python(data)

    def save_index(self, index: SearchIndex) -> None:
        self.write_json_atomic(self.config.index_path, index.model_dump())

    def load_query_cache(self) -> SearchQueryCache:
        if not self.config.query_cache_path.exists():
            return SearchQueryCache(embedding_model=self.config.embedding_model)
        with self.config.query_cache_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return TypeAdapter(SearchQueryCache).validate_python(data)

    def save_query_cache(self, cache: SearchQueryCache) -> None:
        self.write_json_atomic(self.config.query_cache_path, cache.model_dump())

    def write_json_atomic(self, path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temp_path = Path(temp_file.name)
        try:
            with temp_file:
                json.dump(data, temp_file, ensure_ascii=False, indent=2)
                temp_file.write("\n")
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
