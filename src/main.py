import click

from commands.audit import audit
from commands.db import db_commands
from commands.health import check_health
from commands.list import list_entries
from commands.mcp_server import run_mcp_server
from commands.search import search_aggregates
from commands.sync import sync
from commands.tui import launch_tui
from commands.web import serve
from config import load_config
from scripts import scripts


@click.group()
@click.pass_context
def cli(ctx: click.Context):
    """Bonsai Manager CLI"""
    ctx.obj = load_config()


cli.add_command(audit)
cli.add_command(db_commands)
cli.add_command(check_health)
cli.add_command(launch_tui)
cli.add_command(list_entries)
cli.add_command(run_mcp_server)
cli.add_command(scripts)
cli.add_command(search_aggregates)
cli.add_command(serve)
cli.add_command(sync)


if __name__ == "__main__":
    cli()
