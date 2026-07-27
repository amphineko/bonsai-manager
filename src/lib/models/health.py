from typing import Literal

from pydantic import BaseModel


class SearchIndexConsistencyCheck(BaseModel):
    name: Literal["search_index_consistency"] = "search_index_consistency"
    healthy: bool
    sqlite_ready: bool = True
    lancedb_ready: bool = True
    aggregate_count: int
    document_count: int
    missing_documents: list[str]
    orphaned_documents: list[str]
    stale_documents: list[str]
    duplicate_documents: list[str]


class HealthCheckReport(BaseModel):
    healthy: bool
    checks: list[SearchIndexConsistencyCheck]
