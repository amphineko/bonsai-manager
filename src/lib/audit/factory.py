from __future__ import annotations

from collections.abc import Callable

from lib.audit.checks import CollectionCoverageAuditor, TorrentMappingAuditor
from lib.audit.context import AuditContext
from lib.audit.protocols import (
    AggregateAuditor,
    AuditQbittorrentClient,
    CollectionCoverageProvider,
)
from lib.audit.runner import AuditRunner
from lib.models.audit import AuditFinding
from lib.models.bangumi import (
    DEFAULT_BANGUMI_COLLECTION_LOCAL_STATES,
    DEFAULT_BANGUMI_COLLECTION_TYPES,
    BangumiCollectionLocalState,
    BangumiCollectionType,
)
from lib.sql.repositories import AggregateRepository

type AuditorFactory = Callable[[], AggregateAuditor]

AUDITOR_FACTORIES: dict[str, AuditorFactory] = {
    TorrentMappingAuditor.name: TorrentMappingAuditor,
}


class UnknownAuditor:
    def __init__(self, name: str) -> None:
        self.name = name

    def audit(self, context: AuditContext) -> list[AuditFinding]:
        raise ValueError(f"Unknown aggregate auditor: {self.name}")


def create_audit_runner(
    repository: AggregateRepository,
    qbit: AuditQbittorrentClient,
    *,
    categories: tuple[str, ...],
    auditor_names: tuple[str, ...],
    collection_coverage_provider: CollectionCoverageProvider | None = None,
    collection_types: tuple[BangumiCollectionType, ...] = (
        DEFAULT_BANGUMI_COLLECTION_TYPES
    ),
    collection_local_states: tuple[BangumiCollectionLocalState, ...] = (
        DEFAULT_BANGUMI_COLLECTION_LOCAL_STATES
    ),
) -> AuditRunner:
    context = AuditContext(
        repository=repository,
        qbit=qbit,
        categories=categories,
        collection_coverage_provider=collection_coverage_provider,
    )
    auditor_factories = {
        **AUDITOR_FACTORIES,
        CollectionCoverageAuditor.name: lambda: CollectionCoverageAuditor(
            collection_types,
            collection_local_states,
        ),
    }
    return AuditRunner(
        context,
        [
            auditor_factories[name]()
            if name in auditor_factories
            else UnknownAuditor(name)
            for name in auditor_names
        ],
    )
