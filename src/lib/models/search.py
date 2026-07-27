import hashlib

from pydantic import BaseModel, Field

from lib.models.aggregates import Aggregate  # noqa: TC001


class AggregateSearchDocument(BaseModel):
    aggregate_short_name: str
    source_text: str
    source_hash: str
    embedding: list[float]
    model_name: str
    updated_at: str

    @classmethod
    def source_text_from_aggregate(cls, aggregate: Aggregate) -> str:
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

    @classmethod
    def source_hash_from_text(cls, source_text: str) -> str:
        digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    @classmethod
    def from_aggregate(
        cls,
        aggregate: Aggregate,
        embedding: list[float],
        model_name: str,
        updated_at: str,
    ) -> AggregateSearchDocument:
        source_text = cls.source_text_from_aggregate(aggregate)
        return cls(
            aggregate_short_name=aggregate.short_name,
            source_text=source_text,
            source_hash=cls.source_hash_from_text(source_text),
            embedding=embedding,
            model_name=model_name,
            updated_at=updated_at,
        )


class AggregateSearchDocumentMetadata(BaseModel):
    aggregate_short_name: str
    source_hash: str
    model_name: str


class SearchDocumentMatch(BaseModel):
    aggregate_short_name: str
    score: float


class SearchQueryCacheEntry(BaseModel):
    query: str
    query_hash: str
    embedding: list[float]
    model_name: str
    updated_at: str


class SearchQueryCache(BaseModel):
    version: int = 1
    embedding_model: str
    queries: list[SearchQueryCacheEntry] = Field(default_factory=list)


class AggregateSearchResult(BaseModel):
    aggregate: Aggregate
    score: float


class AggregateSearchResults(BaseModel):
    results: list[AggregateSearchResult]


class SearchIndexRebuildResult(BaseModel):
    indexed_documents: int
    force: bool
