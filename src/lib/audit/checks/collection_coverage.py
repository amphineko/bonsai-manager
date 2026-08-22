from __future__ import annotations

from typing import TYPE_CHECKING, cast

from lib.audit.context import AuditContext
from lib.models.audit import AuditFinding, AuditSeverity
from lib.models.bangumi import (
    DEFAULT_BANGUMI_COLLECTION_LOCAL_STATES,
    DEFAULT_BANGUMI_COLLECTION_TYPES,
    BangumiCollectionLocalState,
    BangumiCollectionSubjectCoverage,
    BangumiCollectionType,
)

if TYPE_CHECKING:
    from pydantic import JsonValue


class CollectionCoverageAuditor:
    name = "collection_coverage"

    def __init__(
        self,
        collection_types: tuple[BangumiCollectionType, ...] = (
            DEFAULT_BANGUMI_COLLECTION_TYPES
        ),
        local_states: tuple[BangumiCollectionLocalState, ...] = (
            DEFAULT_BANGUMI_COLLECTION_LOCAL_STATES
        ),
    ) -> None:
        self.collection_types = collection_types
        self.local_states = local_states

    def audit(self, context: AuditContext) -> list[AuditFinding]:
        return [
            self._finding(coverage)
            for coverage in context.get_collection_subject_coverage(
                self.collection_types,
                self.local_states,
            )
        ]

    def _finding(
        self,
        coverage: BangumiCollectionSubjectCoverage,
    ) -> AuditFinding:
        snapshot = coverage.subject.snapshot
        subject_name = snapshot.name if snapshot is not None else ""
        subject_name_cn = snapshot.name_cn if snapshot is not None else ""
        aggregate_names = [aggregate.short_name for aggregate in coverage.aggregates]
        match coverage.local_state:
            case BangumiCollectionLocalState.UNMAPPED:
                code = "collection.unmapped"
                message = "Collected Bangumi subject is not mapped to an aggregate."
                severity = AuditSeverity.WARNING
            case BangumiCollectionLocalState.EMPTY:
                code = "collection.empty"
                message = "Collected Bangumi subject has no tracked torrents."
                severity = AuditSeverity.WARNING
            case BangumiCollectionLocalState.WITH_TORRENTS:
                code = "collection.with_torrents"
                message = "Collected Bangumi subject has tracked torrents."
                severity = AuditSeverity.INFO
        return AuditFinding(
            auditor=self.name,
            code=code,
            severity=severity,
            message=message,
            aggregate_short_name=(
                aggregate_names[0] if len(aggregate_names) == 1 else None
            ),
            metadata={
                "subject_id": coverage.subject.subject_id,
                "subject_name": subject_name,
                "subject_name_cn": subject_name_cn,
                "collection_type": coverage.collection_type.name.lower(),
                "local_state": coverage.local_state.value,
                "aggregates": cast("JsonValue", aggregate_names),
                "torrent_count": coverage.torrent_count,
            },
        )
