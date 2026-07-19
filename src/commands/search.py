from dataclasses import replace

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from config import Config
from lib.services import AggregateService
from lib.models.aggregates import Aggregate
from lib.search import AggregateSearchManager


def format_bangumi_names(entry: Aggregate) -> str:
    names = []
    for subject in entry.bangumi_subjects:
        if subject.snapshot:
            if subject.snapshot.name:
                names.append(subject.snapshot.name)
            if subject.snapshot.name_cn:
                names.append(subject.snapshot.name_cn)
    return "\n".join(names) or "-"


@click.command(name="search")
@click.argument("query_parts", nargs=-1, required=False)
@click.option("--limit", "-n", default=10, show_default=True, help="Maximum results.")
@click.option("--threshold", type=float, default=None, help="Minimum similarity score.")
@click.option(
    "--init",
    "rebuild_index",
    flag_value=True,
    default=False,
    help="Alias for --rebuild-index.",
)
@click.option(
    "--rebuild-index",
    "rebuild_index",
    flag_value=True,
    help="Rebuild the aggregate search index without searching.",
)
@click.option(
    "--force",
    "force_rebuild",
    is_flag=True,
    help="Recompute all aggregate embeddings while rebuilding.",
)
@click.option(
    "--allow-download",
    is_flag=True,
    help="Allow downloading model files from Hugging Face if missing locally.",
)
@click.option(
    "--device",
    default=None,
    help="Embedding device.",
)
@click.pass_obj
def search_aggregates(
    config: Config,
    query_parts: tuple[str, ...],
    limit: int,
    threshold: float | None,
    rebuild_index: bool,
    force_rebuild: bool,
    allow_download: bool,
    device: str | None,
) -> None:
    """Search aggregates semantically with Qwen embeddings."""
    query = " ".join(query_parts).strip()
    if not query and not rebuild_index:
        raise click.UsageError("Search query cannot be empty.")

    manager = AggregateService(config)
    entries = manager.list_aggregates()
    if not entries:
        click.echo("The database is empty.")
        return

    search_config = (
        replace(config.search, embedding_device=device) if device else config.search
    )
    search_manager = AggregateSearchManager(
        config=search_config,
        local_files_only=not allow_download,
    )
    if rebuild_index:
        index = search_manager.rebuild(
            entries,
            force=force_rebuild,
            show_progress=True,
        )
        click.echo(f"Rebuilt search index with {len(index.documents)} aggregates.")
        return

    results = search_manager.search(
        entries,
        query,
        limit=limit,
        threshold=threshold,
    )

    if not results:
        click.echo("No matching aggregates found.")
        return

    console = Console()
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Score", justify="right")
    table.add_column("Category", style="magenta")
    table.add_column("Short Name", style="cyan")
    table.add_column("Bangumi Names", style="green")
    table.add_column("Torrents", justify="right", style="yellow")

    for result in results:
        aggregate = result.aggregate
        table.add_row(
            Text(f"{result.score:.4f}"),
            Text(aggregate.category),
            Text(aggregate.short_name),
            Text(format_bangumi_names(aggregate)),
            Text(str(len(aggregate.torrents))),
        )

    console.print(table)
