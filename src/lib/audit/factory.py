from __future__ import annotations

from collections.abc import Callable

from lib.audit.checks import TorrentMappingAuditor
from lib.audit.context import AuditContext
from lib.audit.protocols import AggregateAuditor, AuditQbittorrentClient
from lib.audit.runner import AuditRunner
from lib.models.audit import AuditFinding
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
) -> AuditRunner:
    context = AuditContext(
        repository=repository,
        qbit=qbit,
        categories=categories,
    )
    return AuditRunner(
        context,
        [
            AUDITOR_FACTORIES[name]()
            if name in AUDITOR_FACTORIES
            else UnknownAuditor(name)
            for name in auditor_names
        ],
    )
