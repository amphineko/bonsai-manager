from __future__ import annotations

from typing import Any, Protocol

import lancedb
import pyarrow as pa

from config import SearchConfig
from lib.models.search import (
    AggregateSearchDocument,
    SearchDocumentMatch,
    SearchQueryCache,
    SearchQueryCacheEntry,
)

DOCUMENTS_TABLE = "aggregate_search_documents"
QUERIES_TABLE = "aggregate_search_queries"


class SearchRepository(Protocol):
    def list_documents(self) -> list[AggregateSearchDocument]: ...

    def replace_documents(self, documents: list[AggregateSearchDocument]) -> None: ...

    def search_documents(
        self,
        query_embedding: list[float],
        limit: int,
        threshold: float | None = None,
    ) -> list[SearchDocumentMatch]: ...

    def load_query_cache(self) -> SearchQueryCache: ...

    def save_query_cache(self, cache: SearchQueryCache) -> None: ...


class LanceDbSearchRepository:
    def __init__(self, config: SearchConfig):
        self.config = config

    def list_documents(self) -> list[AggregateSearchDocument]:
        rows = self.table_rows(DOCUMENTS_TABLE)
        return sorted(
            [
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
            ],
            key=lambda document: document.aggregate_short_name.lower(),
        )

    def replace_documents(self, documents: list[AggregateSearchDocument]) -> None:
        dimension = embedding_dimension(documents)
        rows: list[dict[str, object]] = [
            {
                "version": 1,
                "embedding_model": self.config.embedding_model,
                "aggregate_short_name": document.aggregate_short_name,
                "source_text": document.source_text,
                "source_hash": document.source_hash,
                "vector": document.embedding,
                "model_name": document.model_name,
                "updated_at": document.updated_at,
            }
            for document in documents
        ]
        self.replace_table(DOCUMENTS_TABLE, rows, document_schema(dimension))

    def search_documents(
        self,
        query_embedding: list[float],
        limit: int,
        threshold: float | None = None,
    ) -> list[SearchDocumentMatch]:
        db = self.connect()
        if DOCUMENTS_TABLE not in db.list_tables().tables:
            return []
        rows = (
            db.open_table(DOCUMENTS_TABLE)
            .search(query_embedding, vector_column_name="vector")
            .metric("cosine")
            .limit(limit)
            .to_list()
        )
        matches = [
            SearchDocumentMatch(
                aggregate_short_name=str(row["aggregate_short_name"]),
                score=1.0 - float(row["_distance"]),
            )
            for row in rows
            if row.get("embedding_model") == self.config.embedding_model
        ]
        if threshold is not None:
            matches = [match for match in matches if match.score >= threshold]
        return matches

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


def embedding_dimension(documents: list[AggregateSearchDocument]) -> int:
    dimensions = {len(document.embedding) for document in documents}
    if not dimensions:
        return 1
    if len(dimensions) > 1:
        raise ValueError("Search documents must use a single embedding dimension.")
    return dimensions.pop()


def document_schema(dimension: int) -> pa.Schema:
    return pa.schema(
        [
            pa.field("version", pa.int64()),
            pa.field("embedding_model", pa.string()),
            pa.field("aggregate_short_name", pa.string()),
            pa.field("source_text", pa.string()),
            pa.field("source_hash", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dimension)),
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
