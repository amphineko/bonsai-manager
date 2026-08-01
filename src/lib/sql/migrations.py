from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

RELATED_TABLES = (
    "aggregates",
    "torrents",
    "aggregate_bangumi_subjects",
    "torrent_groups",
)


def aggregate_ids_are_integers(db_path: Path) -> bool:
    with sqlite3.connect(db_path) as connection:
        id_column = next(
            (
                column
                for column in connection.execute("PRAGMA table_info(aggregates)")
                if column[1] == "id"
            ),
            None,
        )
    if id_column is None:
        raise ValueError("SQLite database does not contain aggregates.id.")
    return str(id_column[2]).upper() == "INTEGER"


def migrate_aggregate_ids(
    db_path: Path,
    backup_path: Path,
) -> bool:
    if aggregate_ids_are_integers(db_path):
        return False
    if backup_path.exists():
        raise FileExistsError(f"Migration backup already exists: {backup_path}")

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        sqlite3.connect(db_path) as source,
        sqlite3.connect(backup_path) as backup,
    ):
        source.backup(backup)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            table_names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            required_tables = set(RELATED_TABLES[:-1])
            missing_tables = sorted(required_tables - table_names)
            if missing_tables:
                raise ValueError(
                    "SQLite database is missing required tables: "
                    + ", ".join(missing_tables)
                )

            has_torrent_groups = "torrent_groups" in table_names
            counts_before = {
                table_name: table_count(connection, table_name)
                for table_name in RELATED_TABLES
                if table_name in table_names
            }

            if has_torrent_groups:
                connection.execute(
                    "ALTER TABLE torrent_groups RENAME TO torrent_groups_legacy"
                )
            connection.execute(
                "ALTER TABLE aggregate_bangumi_subjects "
                "RENAME TO aggregate_bangumi_subjects_legacy"
            )
            connection.execute("ALTER TABLE torrents RENAME TO torrents_legacy")
            connection.execute("ALTER TABLE aggregates RENAME TO aggregates_legacy")

            create_integer_aggregate_tables(connection)
            connection.execute(
                "INSERT INTO aggregates (short_name, category) "
                "SELECT short_name, category FROM aggregates_legacy ORDER BY rowid"
            )
            connection.execute(
                "INSERT INTO torrents (hash, aggregate_id) "
                "SELECT torrent.hash, aggregate.id "
                "FROM torrents_legacy AS torrent "
                "JOIN aggregates_legacy AS legacy_aggregate "
                "ON legacy_aggregate.id = torrent.aggregate_id "
                "JOIN aggregates AS aggregate "
                "ON aggregate.short_name = legacy_aggregate.short_name"
            )
            connection.execute(
                "INSERT INTO aggregate_bangumi_subjects "
                "(aggregate_id, subject_id) "
                "SELECT aggregate.id, link.subject_id "
                "FROM aggregate_bangumi_subjects_legacy AS link "
                "JOIN aggregates_legacy AS legacy_aggregate "
                "ON legacy_aggregate.id = link.aggregate_id "
                "JOIN aggregates AS aggregate "
                "ON aggregate.short_name = legacy_aggregate.short_name"
            )
            if has_torrent_groups:
                connection.execute(
                    "INSERT INTO torrent_groups (torrent_hash, group_name) "
                    "SELECT torrent_hash, group_name FROM torrent_groups_legacy"
                )

            counts_after = {
                table_name: table_count(connection, table_name)
                for table_name in RELATED_TABLES
            }
            expected_counts = {
                **counts_before,
                "torrent_groups": counts_before.get("torrent_groups", 0),
            }
            if counts_after != expected_counts:
                raise RuntimeError(
                    f"Migration row counts differ: {counts_before=} {counts_after=}"
                )

            foreign_key_errors = list(connection.execute("PRAGMA foreign_key_check"))
            if foreign_key_errors:
                raise RuntimeError(
                    f"Migration produced foreign key errors: {foreign_key_errors}"
                )

            if has_torrent_groups:
                connection.execute("DROP TABLE torrent_groups_legacy")
            connection.execute("DROP TABLE aggregate_bangumi_subjects_legacy")
            connection.execute("DROP TABLE torrents_legacy")
            connection.execute("DROP TABLE aggregates_legacy")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")
    return True


def table_count(connection: sqlite3.Connection, table_name: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    if row is None:
        raise RuntimeError(f"Could not count rows in {table_name}.")
    return int(row[0])


def create_integer_aggregate_tables(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE aggregates (
            id INTEGER NOT NULL,
            short_name VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (short_name)
        )""",
        """CREATE TABLE torrents (
            hash VARCHAR NOT NULL,
            aggregate_id INTEGER NOT NULL,
            PRIMARY KEY (hash),
            FOREIGN KEY(aggregate_id) REFERENCES aggregates(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE aggregate_bangumi_subjects (
            aggregate_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            PRIMARY KEY (aggregate_id, subject_id),
            FOREIGN KEY(aggregate_id) REFERENCES aggregates(id) ON DELETE CASCADE,
            FOREIGN KEY(subject_id) REFERENCES bangumi_subjects(subject_id)
        )""",
        """CREATE TABLE torrent_groups (
            torrent_hash VARCHAR NOT NULL,
            group_name VARCHAR NOT NULL,
            PRIMARY KEY (torrent_hash),
            CONSTRAINT valid_torrent_group_name CHECK (
                length(trim(group_name)) > 0 AND lower(group_name) <> 'ungrouped'
            ),
            FOREIGN KEY(torrent_hash) REFERENCES torrents(hash) ON DELETE CASCADE
        )""",
    )
    for statement in statements:
        connection.execute(statement)
