from __future__ import annotations

import json
from pathlib import Path

import click
from pydantic import TypeAdapter

from config import Config
from lib.models.aggregates import Aggregate
from lib.sql import SqliteAggregateRepository

DEFAULT_JSON_DB_PATH = Path("db.json")


@click.group(name="db")
def db_commands() -> None:
    """Database migration and validation helpers."""


@db_commands.command(name="import-json")
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Legacy JSON database path. Defaults to db.json.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="SQLite database path. Defaults to active DB_PATH.",
)
@click.pass_obj
def import_json(
    config: Config, input_path: Path | None, output_path: Path | None
) -> None:
    """Import a legacy JSON database into SQLite."""
    json_path = input_path or DEFAULT_JSON_DB_PATH
    sqlite_path = output_path or config.database.path
    require_existing_file(json_path, "JSON database")
    aggregates = load_json_aggregates(json_path)
    validate_aggregates(aggregates)
    sqlite_repo = SqliteAggregateRepository(sqlite_path)
    with sqlite_repo.get_repository(write=True) as repo:
        repo.import_all(aggregates)
    click.echo(f"Imported {len(aggregates)} aggregates into {sqlite_path}")


@db_commands.command(name="validate")
@click.option(
    "--path",
    "path",
    type=click.Path(path_type=Path),
    default=None,
    help="SQLite database path override. Defaults to active DB_PATH.",
)
@click.pass_obj
def validate(config: Config, path: Path | None) -> None:
    """Validate aggregate uniqueness and SQLite loadability."""
    selected_path = path or config.database.path
    require_existing_file(selected_path, "SQLite database")
    sqlite_repo = SqliteAggregateRepository(selected_path, create=False)
    with sqlite_repo.get_repository(write=False) as repo:
        aggregates = repo.list_all()
    validate_aggregates(aggregates)

    torrent_count = sum(len(aggregate.torrents) for aggregate in aggregates)
    subject_count = sum(len(aggregate.bangumi_subjects) for aggregate in aggregates)
    click.echo(
        "Database valid: "
        f"{len(aggregates)} aggregates, "
        f"{torrent_count} torrents, "
        f"{subject_count} Bangumi subjects."
    )


def load_json_aggregates(path: Path) -> list[Aggregate]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return TypeAdapter(list[Aggregate]).validate_python(data)


def require_existing_file(path: Path, label: str) -> None:
    if not path.exists():
        raise click.ClickException(f"{label} not found: {path}")
    if not path.is_file():
        raise click.ClickException(f"{label} is not a file: {path}")


def validate_aggregates(aggregates: list[Aggregate]) -> None:
    short_names = set[str]()
    torrent_hashes = dict[str, str]()
    for aggregate in aggregates:
        if aggregate.short_name in short_names:
            raise click.ClickException(f"Duplicate aggregate: {aggregate.short_name}")
        short_names.add(aggregate.short_name)

        for torrent in aggregate.torrents:
            existing = torrent_hashes.get(torrent.hash)
            if existing is not None:
                raise click.ClickException(
                    f"Duplicate torrent hash {torrent.hash}: "
                    f"{existing} and {aggregate.short_name}"
                )
            torrent_hashes[torrent.hash] = aggregate.short_name
