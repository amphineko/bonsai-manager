from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from config import Config
from lib.services import AggregateService


@click.command(name="audit")
@click.option(
    "--category",
    "-c",
    multiple=True,
    help="Categories to filter (can specify multiple)",
)
@click.pass_obj
def audit_torrent_mapping(config: Config, category: tuple[str, ...]) -> None:
    """Inspect qBittorrent against stored torrent hashes."""
    manager = AggregateService(config)
    try:
        categories = list(category) if category else None
        result = manager.audit_torrent_mapping(categories)
        click.echo(
            "qBittorrent inspection: "
            f"{len(result.tracked_found)} tracked found, "
            f"{len(result.tracked_missing)} tracked missing, "
            f"{len(result.unmapped)} unmapped, "
            f"{len(result.duplicates)} duplicate hashes."
        )

        if result.tracked_missing:
            console = Console()
            table = Table(
                show_header=True,
                header_style="bold red",
                title="Tracked Hashes Missing from qBittorrent",
            )
            table.add_column("Hash", style="dim")
            table.add_column("Aggregates", style="cyan")
            for item in result.tracked_missing:
                table.add_row(Text(item.hash), Text("\n".join(item.aggregates)))
            console.print(table)

        if result.duplicates:
            console = Console()
            table = Table(
                show_header=True,
                header_style="bold red",
                title="Duplicate Hashes in Database",
            )
            table.add_column("Hash", style="dim")
            table.add_column("Aggregates", style="cyan")
            for item in result.duplicates:
                table.add_row(Text(item.hash), Text("\n".join(item.aggregates)))
            console.print(table)

        if result.unmapped:
            console = Console()
            table = Table(
                show_header=True,
                header_style="bold yellow",
                title="Unmapped Torrents Found",
            )
            table.add_column("Category", style="magenta")
            table.add_column("Name", style="cyan")
            table.add_column("Hash", style="dim")
            table.add_column("Save Path", style="green")

            for torrent in result.unmapped:
                table.add_row(
                    Text(torrent.category or "N/A"),
                    Text(torrent.name),
                    Text(torrent.hash),
                    Text(torrent.save_path),
                )
            console.print(table)
        else:
            click.echo("No unmapped torrents found in the specified categories.")

    except RuntimeError as exc:
        click.echo(f"Error during qBittorrent inspection: {exc!s}")
