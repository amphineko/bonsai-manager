from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from config import Config, load_config
from lib.models.aggregates import Aggregate
from lib.models.search import AggregateSearchDocument
from lib.search import AggregateSearchManager
from lib.services import AggregateService

DocumentFormatter = Callable[[Aggregate], str]


def bangumi_names(aggregate: Aggregate) -> list[str]:
    names: list[str] = []
    for subject in aggregate.bangumi_subjects:
        if not subject.snapshot:
            continue
        names.extend([subject.snapshot.name, subject.snapshot.name_cn])
    return [name for name in names if name]


def bangumi_tags(aggregate: Aggregate) -> list[str]:
    tags: list[str] = []
    for subject in aggregate.bangumi_subjects:
        if subject.snapshot:
            tags.extend(tag.name for tag in subject.snapshot.tags)
    return sorted({tag for tag in tags if tag})


def current_document_format(aggregate: Aggregate) -> str:
    return AggregateSearchDocument.source_text_from_aggregate(aggregate)


def compact_document_format(aggregate: Aggregate) -> str:
    parts = [
        aggregate.short_name,
        *bangumi_names(aggregate),
        aggregate.category,
        *bangumi_tags(aggregate),
    ]
    return "\n".join(part for part in parts if part)


def title_first_document_format(aggregate: Aggregate) -> str:
    lines = [
        aggregate.short_name,
        *bangumi_names(aggregate),
    ]
    tags = bangumi_tags(aggregate)
    if tags:
        lines.append("tags: " + ", ".join(tags))
    lines.append("category: " + aggregate.category)
    return "\n".join(line for line in lines if line)


def document_formats() -> list[tuple[str, DocumentFormatter]]:
    return [
        ("current", current_document_format),
        ("compact", compact_document_format),
        ("title-first", title_first_document_format),
    ]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def format_bangumi_names(aggregate: Aggregate) -> str:
    return "\n".join(bangumi_names(aggregate)) or "-"


def render_results(
    query: str,
    rows: list[tuple[str, int, float, Aggregate]],
) -> None:
    console = Console()
    table = Table(
        title=f"Search document distance benchmark: {query}",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Format", style="magenta")
    table.add_column("Rank", justify="right")
    table.add_column("Similarity", justify="right", style="green")
    table.add_column("Distance", justify="right", style="yellow")
    table.add_column("Short Name", style="cyan")
    table.add_column("Bangumi Names")

    for format_name, rank, similarity, aggregate in rows:
        table.add_row(
            Text(format_name),
            Text(str(rank)),
            Text(f"{similarity:.4f}"),
            Text(f"{1.0 - similarity:.4f}"),
            Text(aggregate.short_name),
            Text(format_bangumi_names(aggregate)),
        )

    console.print(table)


@click.command("search-distance-benchmark")
@click.argument("query_parts", nargs=-1, required=True)
@click.option("--limit", "-n", default=5, show_default=True, help="Rows per format.")
@click.option(
    "--filter-short-name",
    multiple=True,
    help="SQLite GLOB pattern for aggregates to benchmark.",
)
@click.option(
    "--allow-download",
    is_flag=True,
    help="Allow downloading model files from Hugging Face if missing locally.",
)
@click.option("--device", default=None, help="Embedding device override.")
@click.pass_obj
def search_distance_benchmark(
    config: Config | None,
    query_parts: tuple[str, ...],
    limit: int,
    filter_short_name: tuple[str, ...],
    allow_download: bool,
    device: str | None,
) -> None:
    """Compare query/document embedding distances across document formats."""
    config = config or load_config()
    query = " ".join(query_parts).strip()
    service = AggregateService(config)
    aggregate_queries = service.queries
    aggregates = aggregate_queries.list_aggregates(
        filter_short_name=list(filter_short_name) or None,
    )
    if not aggregates:
        click.echo("No aggregates found.")
        return

    search_config = (
        replace(config.search, embedding_device=device) if device else config.search
    )
    manager = AggregateSearchManager(
        config=search_config,
        aggregates=aggregate_queries,
        local_files_only=not allow_download,
    )
    query_embedding = manager.encode([query], is_query=True, show_progress=True)[0]

    rows: list[tuple[str, int, float, Aggregate]] = []
    for format_name, formatter in document_formats():
        source_texts = [formatter(aggregate) for aggregate in aggregates]
        document_embeddings = manager.encode(
            source_texts,
            is_query=False,
            show_progress=True,
        )
        scored = sorted(
            [
                (cosine_similarity(query_embedding, embedding), aggregate)
                for aggregate, embedding in zip(
                    aggregates,
                    document_embeddings,
                    strict=True,
                )
            ],
            key=lambda item: item[0],
            reverse=True,
        )
        rows.extend(
            (format_name, rank, similarity, aggregate)
            for rank, (similarity, aggregate) in enumerate(scored[:limit], start=1)
        )

    render_results(query, rows)
