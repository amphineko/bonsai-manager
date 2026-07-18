from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import click

from config import Config
from lib.models.aggregates import Aggregate
from lib.repositories import JsonAggregateRepository, SqliteAggregateRepository

DEFAULT_JSON_DB_PATH = Path("db.json")
DEFAULT_SQLITE_DB_PATH = Path("db.sqlite3")


@click.group(name="db")
def db_commands() -> None:
    """Database migration and validation helpers."""


@db_commands.command(name="import-json")
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path),
    default=None,
    help="JSON database path. Defaults to db.json.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="SQLite database path. Defaults to db.sqlite3.",
)
@click.pass_obj
def import_json(
    config: Config, input_path: Path | None, output_path: Path | None
) -> None:
    """Import the JSON database into SQLite."""
    json_path = input_path or DEFAULT_JSON_DB_PATH
    require_existing_file(json_path, "JSON database")
    sqlite_path = output_path or DEFAULT_SQLITE_DB_PATH
    json_repo = JsonAggregateRepository(json_path)
    sqlite_repo = SqliteAggregateRepository(sqlite_path)
    aggregates = json_repo.list_all()
    validate_aggregates(aggregates)
    sqlite_repo.replace_all(aggregates)
    click.echo(f"Imported {len(aggregates)} aggregates into {sqlite_path}")


@db_commands.command(name="export-json")
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path),
    default=None,
    help="SQLite database path. Defaults to active DB_PATH.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    required=True,
    help="JSON output path.",
)
@click.pass_obj
def export_json(config: Config, input_path: Path | None, output_path: Path) -> None:
    """Export SQLite aggregates to JSON."""
    sqlite_path = input_path or config.database.path
    require_existing_file(sqlite_path, "SQLite database")
    sqlite_repo = SqliteAggregateRepository(sqlite_path, create=False)
    aggregates = sqlite_repo.list_all()
    JsonAggregateRepository(output_path).replace_all(aggregates)
    click.echo(f"Exported {len(aggregates)} aggregates into {output_path}")


@db_commands.command(name="validate")
@click.option(
    "--backend",
    type=click.Choice(["json", "sqlite"]),
    default=None,
    help="Backend to validate. Defaults to DB_BACKEND.",
)
@click.option(
    "--path",
    "path",
    type=click.Path(path_type=Path),
    default=None,
    help="Database path override.",
)
@click.pass_obj
def validate(config: Config, backend: str | None, path: Path | None) -> None:
    """Validate aggregate uniqueness and loadability."""
    selected_backend = backend or config.database.backend
    selected_path = path or config.database.path
    require_existing_file(selected_path, f"{selected_backend} database")
    match selected_backend:
        case "sqlite":
            repo = SqliteAggregateRepository(selected_path, create=False)
        case "json":
            repo = JsonAggregateRepository(selected_path)
        case _:
            raise click.Abort(f"Invalid backend {selected_backend}")

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


@db_commands.command(name="roundtrip-check")
@click.option(
    "--json-input",
    type=click.Path(path_type=Path),
    default=None,
    help="JSON database path. Defaults to db.json.",
)
@click.option(
    "--sqlite-output",
    type=click.Path(path_type=Path),
    default=None,
    help="SQLite database path. Defaults to db.sqlite3.",
)
@click.pass_obj
def roundtrip_check(
    config: Config,
    json_input: Path | None,
    sqlite_output: Path | None,
) -> None:
    """Import JSON into SQLite and compare normalized Pydantic payloads."""
    json_path = json_input or DEFAULT_JSON_DB_PATH
    require_existing_file(json_path, "JSON database")
    with sqlite_roundtrip_repository(sqlite_output) as sqlite_repo:
        json_repo = JsonAggregateRepository(json_path)
        expected = sorted(
            json_repo.list_all(),
            key=lambda aggregate: aggregate.short_name,
        )
        validate_aggregates(expected)
        sqlite_repo.replace_all(expected)
        actual = sqlite_repo.list_all()
    validate_aggregates(actual)
    expected_dump = [normalized_aggregate_dump(aggregate) for aggregate in expected]
    actual_dump = [normalized_aggregate_dump(aggregate) for aggregate in actual]
    if actual_dump != expected_dump:
        raise click.ClickException("SQLite roundtrip changed aggregate payloads.")
    click.echo(f"Roundtrip valid: {len(actual)} aggregates.")


def require_existing_file(path: Path, label: str) -> None:
    if not path.exists():
        raise click.ClickException(f"{label} not found: {path}")
    if not path.is_file():
        raise click.ClickException(f"{label} is not a file: {path}")


class sqlite_roundtrip_repository:
    def __init__(self, output_path: Path | None):
        self.output_path = output_path
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> SqliteAggregateRepository:
        if self.output_path is not None:
            return SqliteAggregateRepository(self.output_path)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="bonsai-roundtrip-")
        return SqliteAggregateRepository(Path(self.temp_dir.name) / "roundtrip.sqlite3")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()


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


def normalized_aggregate_dump(aggregate: Aggregate) -> dict[str, Any]:
    data = aggregate.model_dump(mode="json")
    data["bangumi_subjects"] = sorted(
        data["bangumi_subjects"],
        key=lambda subject: subject["subject_id"],
    )
    data["torrents"] = sorted(
        data["torrents"],
        key=lambda torrent: torrent["hash"],
    )
    return data
