import click

from scripts.mcp_search_aggregates import mcp_search_aggregates as mcp_search_aggregates


@click.group("scripts")
def scripts():
    pass


scripts.add_command(mcp_search_aggregates)
