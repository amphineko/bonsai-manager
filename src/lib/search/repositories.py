from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Protocol

import lancedb
import pyarrow as pa
from pydantic import TypeAdapter

from config import SearchConfig
from lib.models.search import (
    AggregateSearchDocument,
    SearchIndex,
    SearchQueryCache,
    SearchQueryCacheEntry,
)

DOCUMENTS_TABLE = "aggregate_search_documents"
QUERIES_TABLE = "aggregate_search_queries"


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


class LanceDbSearchRepository:
    def __init__(self, config: SearchConfig):
        self.config = config

    def load_index(self) -> SearchIndex:
        rows = self.table_rows(DOCUMENTS_TABLE)
        documents = [
            AggregateSearchDocument(
                aggregate_short_name=str(row["aggregate_short_name"]),
                source_text=str(row["source_text"]),
                source_hash=str(row["source_hash"]),
                embedding=[float(value) for value in row["vector"]],
                model_name=str(row["model_name"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
            if row.get("embedding_model") == self.config.embedding_model
        ]
        return SearchIndex(
            embedding_model=self.config.embedding_model,
            documents=sorted(
                documents,
                key=lambda document: document.aggregate_short_name.lower(),
            ),
        )

    def save_index(self, index: SearchIndex) -> None:
        rows: list[dict[str, object]] = [
            {
                "version": index.version,
                "embedding_model": index.embedding_model,
                "aggregate_short_name": document.aggregate_short_name,
                "source_text": document.source_text,
                "source_hash": document.source_hash,
                "vector": document.embedding,
                "model_name": document.model_name,
                "updated_at": document.updated_at,
            }
            for document in index.documents
        ]
        self.replace_table(DOCUMENTS_TABLE, rows, document_schema())

    def load_query_cache(self) -> SearchQueryCache:
        rows = self.table_rows(QUERIES_TABLE)
        queries = [
            SearchQueryCacheEntry(
                query=str(row["query"]),
                query_hash=str(row["query_hash"]),
                embedding=[float(value) for value in row["vector"]],
                model_name=str(row["model_name"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
            if row.get("embedding_model") == self.config.embedding_model
        ]
        return SearchQueryCache(
            embedding_model=self.config.embedding_model,
            queries=sorted(queries, key=lambda entry: entry.query),
        )

    def save_query_cache(self, cache: SearchQueryCache) -> None:
        rows: list[dict[str, object]] = [
            {
                "version": cache.version,
                "embedding_model": cache.embedding_model,
                "query": entry.query,
                "query_hash": entry.query_hash,
                "vector": entry.embedding,
                "model_name": entry.model_name,
                "updated_at": entry.updated_at,
            }
            for entry in cache.queries
        ]
        self.replace_table(QUERIES_TABLE, rows, query_schema())

    def table_rows(self, name: str) -> list[dict[str, Any]]:
        db = self.connect()
        if name not in db.list_tables().tables:
            return []
        return list(db.open_table(name).to_arrow().to_pylist())

    def replace_table(
        self,
        name: str,
        rows: list[dict[str, object]],
        schema: pa.Schema,
    ) -> None:
        db = self.connect()
        db.create_table(name, data=rows, schema=schema, mode="overwrite")

    def connect(self) -> Any:
        self.config.lancedb_path.mkdir(parents=True, exist_ok=True)
        return lancedb.connect(str(self.config.lancedb_path))


def create_search_repository(config: SearchConfig) -> SearchRepository:
    match config.backend:
        case "json":
            return JsonSearchRepository(config)
        case "lancedb":
            return LanceDbSearchRepository(config)
        case _:
            raise ValueError(f"Unsupported SEARCH_BACKEND: {config.backend}")


def document_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("version", pa.int64()),
            pa.field("embedding_model", pa.string()),
            pa.field("aggregate_short_name", pa.string()),
            pa.field("source_text", pa.string()),
            pa.field("source_hash", pa.string()),
            pa.field("vector", pa.list_(pa.float32())),
            pa.field("model_name", pa.string()),
            pa.field("updated_at", pa.string()),
        ]
    )


def query_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("version", pa.int64()),
            pa.field("embedding_model", pa.string()),
            pa.field("query", pa.string()),
            pa.field("query_hash", pa.string()),
            pa.field("vector", pa.list_(pa.float32())),
            pa.field("model_name", pa.string()),
            pa.field("updated_at", pa.string()),
        ]
    )
