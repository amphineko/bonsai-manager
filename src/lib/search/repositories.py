from __future__ import annotations

from typing import Any, Protocol

import lancedb
import pyarrow as pa

from config import SearchConfig
from lib.models.search import (
    AggregateSearchDocument,
    AggregateSearchDocumentMetadata,
    SearchDocumentMatch,
    SearchQueryCache,
    SearchQueryCacheEntry,
)

DOCUMENTS_TABLE = "aggregate_search_documents"
QUERIES_TABLE = "aggregate_search_queries"


class SearchRepository(Protocol):
    def list_documents(self) -> list[AggregateSearchDocument]: ...

    def list_document_metadata(self) -> list[AggregateSearchDocumentMetadata]: ...

    def count_documents(self) -> int: ...

    def replace_documents(self, documents: list[AggregateSearchDocument]) -> None: ...

    def upsert_document(self, document: AggregateSearchDocument) -> None: ...

    def delete_document(self, aggregate_short_name: str) -> None: ...

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

    def count_documents(self) -> int:
        db = self.connect()
        if DOCUMENTS_TABLE not in db.list_tables().tables:
            return 0
        return int(
            db.open_table(DOCUMENTS_TABLE).count_rows(
                f"embedding_model = {lance_sql_string(self.config.embedding_model)}"
            )
        )

    def documents_table_exists(self) -> bool:
        if not self.config.lancedb_path.is_dir():
            return False
        try:
            db = lancedb.connect(str(self.config.lancedb_path))
            if DOCUMENTS_TABLE not in db.list_tables().tables:
                return False
            table = db.open_table(DOCUMENTS_TABLE)
            if not document_schema_is_compatible(table.schema):
                return False
            table.count_rows()
        except OSError, RuntimeError, TypeError, ValueError, pa.ArrowException:
            # Readiness checks report inaccessible or corrupt stores as unhealthy.
            return False
        return True

    def list_document_metadata(self) -> list[AggregateSearchDocumentMetadata]:
        db = self.connect()
        if DOCUMENTS_TABLE not in db.list_tables().tables:
            return []
        rows = (
            db.open_table(DOCUMENTS_TABLE)
            .search()
            .where(f"embedding_model = {lance_sql_string(self.config.embedding_model)}")
            .select(["aggregate_short_name", "source_hash", "model_name"])
            .to_list()
        )
        return sorted(
            [AggregateSearchDocumentMetadata.model_validate(row) for row in rows],
            key=lambda document: document.aggregate_short_name.lower(),
        )

    def replace_documents(self, documents: list[AggregateSearchDocument]) -> None:
        dimension = embedding_dimension(documents)
        rows = [self.document_row(document) for document in documents]
        self.replace_table(DOCUMENTS_TABLE, rows, document_schema(dimension))

    def upsert_document(self, document: AggregateSearchDocument) -> None:
        db = self.connect()
        row = self.document_row(document)
        if DOCUMENTS_TABLE not in db.list_tables().tables:
            db.create_table(
                DOCUMENTS_TABLE,
                data=[row],
                schema=document_schema(len(document.embedding)),
                mode="overwrite",
            )
            return

        table = db.open_table(DOCUMENTS_TABLE)
        if table.count_rows() == 0:
            db.create_table(
                DOCUMENTS_TABLE,
                data=[row],
                schema=document_schema(len(document.embedding)),
                mode="overwrite",
            )
            return

        table.delete(
            document_filter(
                self.config.embedding_model,
                document.aggregate_short_name,
            )
        )
        table.add([row])

    def delete_document(self, aggregate_short_name: str) -> None:
        db = self.connect()
        if DOCUMENTS_TABLE not in db.list_tables().tables:
            return
        db.open_table(DOCUMENTS_TABLE).delete(
            document_filter(self.config.embedding_model, aggregate_short_name)
        )

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
            .where(f"embedding_model = {lance_sql_string(self.config.embedding_model)}")
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

    def document_row(self, document: AggregateSearchDocument) -> dict[str, object]:
        return {
            "version": 1,
            "embedding_model": self.config.embedding_model,
            "aggregate_short_name": document.aggregate_short_name,
            "source_text": document.source_text,
            "source_hash": document.source_hash,
            "vector": document.embedding,
            "model_name": document.model_name,
            "updated_at": document.updated_at,
        }

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


def lance_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def document_filter(embedding_model: str, aggregate_short_name: str) -> str:
    return " AND ".join(
        [
            "embedding_model = " + lance_sql_string(embedding_model),
            "aggregate_short_name = " + lance_sql_string(aggregate_short_name),
        ]
    )


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


def document_schema_is_compatible(schema: pa.Schema) -> bool:
    required_types = {
        "version": pa.int64(),
        "embedding_model": pa.string(),
        "aggregate_short_name": pa.string(),
        "source_text": pa.string(),
        "source_hash": pa.string(),
        "model_name": pa.string(),
        "updated_at": pa.string(),
    }
    if any(
        name not in schema.names or schema.field(name).type != expected_type
        for name, expected_type in required_types.items()
    ):
        return False
    if "vector" not in schema.names:
        return False
    vector_type = schema.field("vector").type
    return (
        pa.types.is_fixed_size_list(vector_type)
        and vector_type.value_type == pa.float32()
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
