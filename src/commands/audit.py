from __future__ import annotations

import click
from rich.pretty import pprint

from config import Config
from lib.models.audit import AuditReport
from lib.services import AggregateService


@click.command(name="audit")
@click.option(
    "--category",
    "-c",
    multiple=True,
    help="Categories to filter (can specify multiple)",
)
@click.pass_obj
def audit(config: Config, category: tuple[str, ...]) -> AuditReport:
    """Run configured read-only aggregate audit checks."""
    manager = AggregateService(config)
    try:
        categories = list(category) if category else None
        report = manager.run_audit(categories)
    finally:
        manager.close()

    pprint(report)
    if not report.successful:
        raise click.exceptions.Exit(1)
    return report
