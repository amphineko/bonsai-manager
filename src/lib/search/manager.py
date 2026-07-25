from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from sentence_transformers import SentenceTransformer
from tqdm.auto import tqdm

from lib.models.search import (
    AggregateSearchDocument,
    AggregateSearchResult,
    SearchQueryCache,
    SearchQueryCacheEntry,
)
from lib.search.repositories import LanceDbSearchRepository, SearchRepository

if TYPE_CHECKING:
    from config import SearchConfig
    from lib.models.aggregates import Aggregate


class AggregateProvider(Protocol):
    def list_aggregates(self) -> list[Aggregate]: ...

    def get_by_short_names(self, short_names: list[str]) -> list[Aggregate]: ...


class AggregateSearchManager:
    def __init__(
        self,
        config: SearchConfig,
        aggregates: AggregateProvider,
        local_files_only: bool = True,
        repository: SearchRepository | None = None,
    ):
        self.config = config
        self.aggregates = aggregates
        self.repository = repository or LanceDbSearchRepository(config)
        self.local_files_only = local_files_only
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(
                self.config.embedding_model,
                device=self.config.embedding_device,
                local_files_only=self.local_files_only,
            )
        return self._model

    def list_documents(self) -> list[AggregateSearchDocument]:
        return self.repository.list_documents()

    def count_documents(self) -> int:
        return self.repository.count_documents()

    def load_query_cache(self) -> SearchQueryCache:
        return self.repository.load_query_cache()

    def save_query_cache(self, cache: SearchQueryCache) -> None:
        self.repository.save_query_cache(cache)

    def query_hash(self, query: str) -> str:
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def encode(
        self,
        texts: list[str],
        is_query: bool,
        show_progress: bool = False,
    ) -> list[list[float]]:
        if (
            is_query
            and self.config.embedding_query_prompt_model_marker
            in self.config.embedding_model
        ):
            embeddings = self.model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=show_progress,
                prompt_name="query",
            )
        else:
            embeddings = self.model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=show_progress,
            )
        if hasattr(embeddings, "tolist"):
            embeddings = embeddings.tolist()
        return [[float(value) for value in row] for row in embeddings]

    def get_query_embedding(
        self,
        query: str,
        show_progress: bool = False,
    ) -> list[float]:
        cache = self.load_query_cache()
        query_hash = self.query_hash(query)
        if cache.embedding_model == self.config.embedding_model:
            for entry in cache.queries:
                if (
                    entry.query_hash == query_hash
                    and entry.query == query
                    and entry.model_name == self.config.embedding_model
                ):
                    return entry.embedding

        embedding = self.encode([query], is_query=True, show_progress=show_progress)[0]
        now = datetime.now(UTC).isoformat()
        existing_entries = [
            entry
            for entry in cache.queries
            if not (
                entry.query_hash == query_hash
                and entry.query == query
                and entry.model_name == self.config.embedding_model
            )
        ]
        existing_entries.append(
            SearchQueryCacheEntry(
                query=query,
                query_hash=query_hash,
                embedding=embedding,
                model_name=self.config.embedding_model,
                updated_at=now,
            )
        )
        self.save_query_cache(
            SearchQueryCache(
                version=1,
                embedding_model=self.config.embedding_model,
                queries=sorted(existing_entries, key=lambda entry: entry.query),
            )
        )
        return embedding

    def rebuild(
        self,
        force: bool = False,
        show_progress: bool = False,
    ) -> list[AggregateSearchDocument]:
        aggregates = self.aggregates.list_aggregates()
        existing_documents = self.repository.list_documents()
        old_documents = {
            document.aggregate_short_name: document for document in existing_documents
        }

        new_documents: list[AggregateSearchDocument] = []
        stale_items: list[tuple[Aggregate, str]] = []
        aggregate_items = tqdm(
            aggregates,
            desc="Checking search documents",
            unit="aggregate",
            disable=not show_progress,
        )
        for aggregate in aggregate_items:
            source_text = AggregateSearchDocument.source_text_from_aggregate(aggregate)
            source_hash = AggregateSearchDocument.source_hash_from_text(source_text)
            old_document = old_documents.get(aggregate.short_name)
            if (
                not force
                and old_document
                and old_document.source_hash == source_hash
                and old_document.model_name == self.config.embedding_model
            ):
                new_documents.append(old_document)
            else:
                stale_items.append((aggregate, source_text))

        if stale_items:
            embeddings = self.encode(
                [source_text for _, source_text in stale_items],
                is_query=False,
                show_progress=show_progress,
            )
            now = datetime.now(UTC).isoformat()
            document_items = tqdm(
                zip(stale_items, embeddings, strict=True),
                total=len(stale_items),
                desc="Updating search documents",
                unit="document",
                disable=not show_progress,
            )
            for (aggregate, _source_text), embedding in document_items:
                new_documents.append(
                    AggregateSearchDocument.from_aggregate(
                        aggregate=aggregate,
                        embedding=embedding,
                        model_name=self.config.embedding_model,
                        updated_at=now,
                    )
                )

        documents = sorted(
            new_documents,
            key=lambda document: document.aggregate_short_name.lower(),
        )
        self.repository.replace_documents(documents)
        return documents

    def index_aggregate(self, aggregate: Aggregate) -> AggregateSearchDocument:
        embedding = self.encode(
            [AggregateSearchDocument.source_text_from_aggregate(aggregate)],
            is_query=False,
        )[0]
        document = AggregateSearchDocument.from_aggregate(
            aggregate=aggregate,
            embedding=embedding,
            model_name=self.config.embedding_model,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self.repository.upsert_document(document)
        return document

    def delete_aggregate(self, short_name: str) -> None:
        self.repository.delete_document(short_name)

    def search(
        self,
        query: str,
        limit: int = 10,
        threshold: float | None = None,
        show_progress: bool = False,
    ) -> list[AggregateSearchResult]:
        if self.repository.count_documents() == 0:
            raise ValueError(
                "Search index is empty. Run `search --rebuild-index` first."
            )
        query_embedding = self.get_query_embedding(
            query,
            show_progress=show_progress,
        )
        matches = self.repository.search_documents(
            query_embedding=query_embedding,
            limit=limit,
            threshold=threshold,
        )
        aggregate_by_short_name = {
            aggregate.short_name: aggregate
            for aggregate in self.aggregates.get_by_short_names(
                [match.aggregate_short_name for match in matches]
            )
        }

        results = []
        for match in matches:
            aggregate = aggregate_by_short_name.get(match.aggregate_short_name)
            if aggregate is None:
                continue
            results.append(
                AggregateSearchResult(aggregate=aggregate, score=match.score)
            )

        return results
