import click

from scripts.mcp_e2e import mcp_e2e as mcp_e2e
from scripts.mcp_search_aggregates import mcp_search_aggregates as mcp_search_aggregates
from scripts.search_e2e import search_e2e as search_e2e


@click.group("scripts")
def scripts():
    pass


scripts.add_command(mcp_e2e)
scripts.add_command(mcp_search_aggregates)
scripts.add_command(search_e2e)
