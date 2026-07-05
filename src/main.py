import click

from commands.list import list_entries
from commands.mcp_server import run_mcp_server
from commands.search import search_aggregates
from commands.audit import audit_torrent_mapping
from commands.tui import launch_tui
from commands.web import serve
from config import Config, load_config
from scripts import scripts


@click.group()
@click.pass_context
def cli(ctx: click.Context):
    """Bonsai Manager CLI"""
    ctx.obj = load_config()


@cli.command(name="mcp")
@click.pass_obj
def mcp(config: Config):
    """Run the Bonsai Manager MCP server over stdio."""
    run_mcp_server(config)


cli.add_command(audit_torrent_mapping)
cli.add_command(launch_tui)
cli.add_command(list_entries)
cli.add_command(scripts)
cli.add_command(search_aggregates)
cli.add_command(serve)


if __name__ == "__main__":
    cli()
