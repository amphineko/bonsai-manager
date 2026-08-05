from __future__ import annotations

import click
from rich.pretty import pprint

from config import Config
from lib.models.sync import SyncReport
from lib.services import IndexedAggregateService
from lib.sync import create_sync_runner


@click.command(name="sync")
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Refresh remote data regardless of TTL and recompute all aggregate embeddings."
    ),
)
@click.option(
    "--no-audit",
    "audit_enabled",
    flag_value=False,
    default=True,
    help="Skip configured read-only audit checks.",
)
@click.pass_obj
def sync(
    config: Config,
    force: bool,
    audit_enabled: bool,
) -> SyncReport:
    """Refresh configured sources, repair derived state, and run audits."""
    health_before = IndexedAggregateService.check_health_from_config(config)
    indexed = IndexedAggregateService.from_config(config)
    try:
        report = create_sync_runner(
            indexed,
            force=force,
            audit_enabled=audit_enabled,
            show_progress=True,
            health_before=health_before,
        ).run()
    finally:
        indexed.close()

    pprint(report)
    if not report.healthy:
        raise click.exceptions.Exit(1)
    return report
