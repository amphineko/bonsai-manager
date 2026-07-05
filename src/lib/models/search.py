from typing import List

from pydantic import BaseModel, Field

from lib.models.aggregates import Aggregate


class AggregateSearchDocument(BaseModel):
    aggregate_short_name: str
    source_text: str
    source_hash: str
    embedding: List[float]
    model_name: str
    updated_at: str


class SearchIndex(BaseModel):
    version: int = 1
    embedding_model: str
    documents: List[AggregateSearchDocument] = Field(default_factory=list)


class SearchQueryCacheEntry(BaseModel):
    query: str
    query_hash: str
    embedding: List[float]
    model_name: str
    updated_at: str


class SearchQueryCache(BaseModel):
    version: int = 1
    embedding_model: str
    queries: List[SearchQueryCacheEntry] = Field(default_factory=list)


class AggregateSearchResult(BaseModel):
    aggregate: Aggregate
    score: float


class AggregateSearchResults(BaseModel):
    results: List[AggregateSearchResult]
