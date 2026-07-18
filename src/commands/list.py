import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from config import Config
from lib.db import AggregateService
from lib.models.aggregates import Aggregate


def get_bangumi_name_cn(entry: Aggregate) -> str:
    return "\n".join(
        subject.snapshot.name_cn
        for subject in entry.bangumi_subjects
        if subject.snapshot
    )


def get_bangumi_subject_id(entry: Aggregate) -> str:
    if not entry.bangumi_subjects:
        return "-"
    return "\n".join(str(subject.subject_id) for subject in entry.bangumi_subjects)


def get_last_synced(entry: Aggregate) -> str:
    if not entry.bangumi_subjects:
        return "-"
    return max(subject.last_updated_at for subject in entry.bangumi_subjects)[:19]


@click.command(name="list")
@click.option(
    "--filter",
    "-f",
    "filter_str",
    help="Filter entries by short_name or name_cn (case-insensitive)",
)
@click.pass_obj
def list_entries(config: Config, filter_str: str | None) -> None:
    """Show a tabular summary of tracked entries for humans."""
    manager = AggregateService(config)
    entries = manager.list_all()
    if not entries:
        click.echo("The database is empty.")
        return

    if filter_str:
        filter_str = filter_str.lower()
        entries = [
            entry
            for entry in entries
            if filter_str in entry.short_name.lower()
            or filter_str in get_bangumi_name_cn(entry).lower()
            or filter_str in entry.category.lower()
        ]
        if not entries:
            click.echo(f"No entries found matching: {filter_str}")
            return

    console = Console()
    table = Table(show_header=True, header_style="bold magenta")

    table.add_column("Category", style="magenta")
    table.add_column("Short Name", style="cyan")
    table.add_column("Chinese Name", style="green")
    table.add_column("Bangumi ID", justify="right")
    table.add_column("Last Updated", justify="center")
    table.add_column("Torrents", style="yellow")

    for entry in entries:
        torrent_paths = [
            Text(manager.get_torrent_display_path(torrent))
            for torrent in entry.torrents
        ]
        torrents_text = Text("\n").join(torrent_paths)

        table.add_row(
            Text(entry.category),
            Text(entry.short_name),
            Text(get_bangumi_name_cn(entry) or "-"),
            Text(get_bangumi_subject_id(entry)),
            Text(get_last_synced(entry)),
            torrents_text,
        )

    console.print(table)
