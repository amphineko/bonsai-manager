from __future__ import annotations

import click
from rich.pretty import pprint

from config import Config
from lib.models.health import HealthCheckReport
from lib.services import IndexedAggregateService


@click.command(name="health")
@click.pass_obj
def check_health(config: Config) -> HealthCheckReport:
    """Run service health checks."""
    report = IndexedAggregateService.check_health_from_config(config)
    pprint(report)
    if not report.healthy:
        raise click.exceptions.Exit(1)
    return report
