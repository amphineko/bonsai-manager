from __future__ import annotations

import hashlib
import math
from datetime import datetime

from sentence_transformers import SentenceTransformer

from config import SearchConfig
from lib.models.aggregates import Aggregate
from lib.models.search import (
    AggregateSearchDocument,
    AggregateSearchResult,
    SearchIndex,
    SearchQueryCache,
    SearchQueryCacheEntry,
)
from lib.search.repositories import JsonSearchRepository, SearchRepository


class AggregateSearchManager:
    def __init__(
        self,
        config: SearchConfig,
        local_files_only: bool = True,
        repository: SearchRepository | None = None,
    ):
        self.config = config
        self.repository = repository or JsonSearchRepository(config)
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

    def load_index(self) -> SearchIndex:
        return self.repository.load_index()

    def save_index(self, index: SearchIndex) -> None:
        self.repository.save_index(index)

    def load_query_cache(self) -> SearchQueryCache:
        return self.repository.load_query_cache()

    def save_query_cache(self, cache: SearchQueryCache) -> None:
        self.repository.save_query_cache(cache)

    def source_text_for_aggregate(self, aggregate: Aggregate) -> str:
        lines = [
            f"short_name: {aggregate.short_name}",
            f"category: {aggregate.category}",
        ]
        for subject in aggregate.bangumi_subjects:
            lines.append(f"bangumi_subject_id: {subject.subject_id}")
            if subject.snapshot:
                lines.append(f"bangumi_name: {subject.snapshot.name}")
                lines.append(f"bangumi_cn_name: {subject.snapshot.name_cn}")
                if subject.snapshot.tags:
                    lines.append(
                        "bangumi_tags: "
                        + ", ".join(tag.name for tag in subject.snapshot.tags)
                    )
        return "\n".join(line for line in lines if not line.endswith(": "))

    def source_hash(self, source_text: str) -> str:
        digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def query_hash(self, query: str) -> str:
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def encode(self, texts: list[str], is_query: bool) -> list[list[float]]:
        if (
            is_query
            and self.config.embedding_query_prompt_model_marker
            in self.config.embedding_model
        ):
            embeddings = self.model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
                prompt_name="query",
            )
        else:
            embeddings = self.model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        if hasattr(embeddings, "tolist"):
            embeddings = embeddings.tolist()
        return [[float(value) for value in row] for row in embeddings]

    def get_query_embedding(self, query: str) -> list[float]:
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

        embedding = self.encode([query], is_query=True)[0]
        now = datetime.now().isoformat()
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

    def refresh_index(
        self,
        aggregates: list[Aggregate],
        force: bool = False,
    ) -> SearchIndex:
        old_index = self.load_index()
        old_documents = {
            document.aggregate_short_name: document
            for document in old_index.documents
            if old_index.embedding_model == self.config.embedding_model
        }

        new_documents: list[AggregateSearchDocument] = []
        stale_items: list[tuple[Aggregate, str, str]] = []
        for aggregate in aggregates:
            source_text = self.source_text_for_aggregate(aggregate)
            source_hash = self.source_hash(source_text)
            old_document = old_documents.get(aggregate.short_name)
            if (
                not force
                and old_document
                and old_document.source_hash == source_hash
                and old_document.model_name == self.config.embedding_model
            ):
                new_documents.append(old_document)
            else:
                stale_items.append((aggregate, source_text, source_hash))

        if stale_items:
            embeddings = self.encode(
                [source_text for _, source_text, _ in stale_items],
                is_query=False,
            )
            now = datetime.now().isoformat()
            for (aggregate, source_text, source_hash), embedding in zip(
                stale_items,
                embeddings,
            ):
                new_documents.append(
                    AggregateSearchDocument(
                        aggregate_short_name=aggregate.short_name,
                        source_text=source_text,
                        source_hash=source_hash,
                        embedding=embedding,
                        model_name=self.config.embedding_model,
                        updated_at=now,
                    )
                )

        index = SearchIndex(
            version=1,
            embedding_model=self.config.embedding_model,
            documents=sorted(
                new_documents,
                key=lambda document: document.aggregate_short_name.lower(),
            ),
        )
        self.save_index(index)
        return index

    def search(
        self,
        aggregates: list[Aggregate],
        query: str,
        limit: int = 10,
        threshold: float | None = None,
        force_refresh: bool = False,
    ) -> list[AggregateSearchResult]:
        index = self.refresh_index(aggregates, force=force_refresh)
        aggregate_by_short_name = {
            aggregate.short_name: aggregate for aggregate in aggregates
        }
        query_embedding = self.get_query_embedding(query)

        results = []
        for document in index.documents:
            aggregate = aggregate_by_short_name.get(document.aggregate_short_name)
            if aggregate is None:
                continue
            score = self.dot(query_embedding, document.embedding)
            if threshold is None or score >= threshold:
                results.append(AggregateSearchResult(aggregate=aggregate, score=score))

        results.sort(key=lambda result: result.score, reverse=True)
        return results[:limit]

    def dot(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        if len(left) != len(right):
            return self.cosine(left, right)
        return sum(a * b for a, b in zip(left, right))

    def cosine(self, left: list[float], right: list[float]) -> float:
        denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(
            sum(b * b for b in right)
        )
        if denominator == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / denominator
