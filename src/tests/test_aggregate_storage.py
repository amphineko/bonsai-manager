from __future__ import annotations

import unittest
from typing import override

from lib.models.aggregates import Aggregate, Torrent
from lib.models.bangumi import BangumiSubject, BangumiSubjectSnapshot, BangumiTag
from lib.models.qbittorrent import QbittorrentTorrent
from lib.qbittorrent import QbittorrentClient
from lib.services.torrents import AggregateTorrentService
from lib.sql.repositories import SqliteAggregateRepository

HASH_A = "a" * 40
HASH_B = "b" * 40
HASH_C = "c" * 40
HASH_D = "d" * 40


class StubQbittorrentClient(QbittorrentClient):
    def __init__(self, available_hashes: set[str]) -> None:
        self.available_hashes = available_hashes
        self.login_count = 0
        self.requested_hashes: list[str] = []

    @override
    def close(self) -> None:
        pass

    @override
    def login(self) -> None:
        self.login_count += 1

    @override
    def get_torrents_info(
        self,
        torrent_hashes: list[str],
    ) -> list[QbittorrentTorrent]:
        self.requested_hashes.extend(torrent_hashes)
        return [
            QbittorrentTorrent(hash=torrent_hash.upper(), name=torrent_hash)
            for torrent_hash in torrent_hashes
            if torrent_hash in self.available_hashes
        ]


class SqliteAggregateTestCase(unittest.TestCase):
    repository: SqliteAggregateRepository

    @override
    def setUp(self) -> None:
        self.repository = SqliteAggregateRepository(":memory:")

    @override
    def tearDown(self) -> None:
        self.repository.close()

    def add_aggregate(self, aggregate: Aggregate | None = None) -> Aggregate:
        selected = aggregate or aggregate_fixture()
        with self.repository.get_repository(write=True) as repo:
            repo.add(selected)
        return selected


class SqliteAggregateRepositoryTest(SqliteAggregateTestCase):
    def test_round_trip_uses_integer_relationships(self) -> None:
        expected = self.add_aggregate()

        with self.repository.get_repository(write=False) as repo:
            actual = repo.get_by_short_name(expected.short_name)

        self.assertEqual(actual, expected)
        if self.repository.engine is None:
            self.fail("Root repository has no SQLite engine.")
        with self.repository.engine.connect() as connection:
            aggregate_id = tuple(
                connection.exec_driver_sql(
                    "SELECT typeof(id), id FROM aggregates"
                ).one()
            )
            relationship_types = tuple(
                connection.exec_driver_sql(
                    """
                    SELECT
                        (SELECT typeof(aggregate_id) FROM torrents LIMIT 1),
                        (SELECT typeof(aggregate_id)
                         FROM aggregate_bangumi_subjects LIMIT 1)
                    """
                ).one()
            )
            foreign_key_errors = list(
                connection.exec_driver_sql("PRAGMA foreign_key_check")
            )

        self.assertEqual(aggregate_id, ("integer", 1))
        self.assertEqual(relationship_types, ("integer", "integer"))
        self.assertEqual(foreign_key_errors, [])

    def test_write_transaction_rolls_back_on_error(self) -> None:
        with (
            self.assertRaisesRegex(RuntimeError, "abort transaction"),
            self.repository.get_repository(write=True) as repo,
        ):
            repo.add(aggregate_fixture())
            raise RuntimeError("abort transaction")

        with self.repository.get_repository(write=False) as repo:
            self.assertEqual(repo.count_aggregates(), 0)

    def test_removal_cascades_relationships_but_retains_subject(self) -> None:
        expected = self.add_aggregate()

        with self.repository.get_repository(write=True) as repo:
            removed = repo.remove_by_short_name(expected.short_name)

        self.assertEqual(removed, expected)
        if self.repository.engine is None:
            self.fail("Root repository has no SQLite engine.")
        with self.repository.engine.connect() as connection:
            counts = {
                table: connection.exec_driver_sql(
                    f"SELECT COUNT(*) FROM {table}"
                ).scalar_one()
                for table in (
                    "aggregates",
                    "aggregate_bangumi_subjects",
                    "torrents",
                    "torrent_groups",
                    "bangumi_subjects",
                )
            }
            foreign_key_errors = list(
                connection.exec_driver_sql("PRAGMA foreign_key_check")
            )

        self.assertEqual(
            counts,
            {
                "aggregates": 0,
                "aggregate_bangumi_subjects": 0,
                "torrents": 0,
                "torrent_groups": 0,
                "bangumi_subjects": 1,
            },
        )
        self.assertEqual(foreign_key_errors, [])


class AggregateTorrentServiceTest(SqliteAggregateTestCase):
    qbit: StubQbittorrentClient
    service: AggregateTorrentService

    @override
    def setUp(self) -> None:
        super().setUp()
        self.add_aggregate(
            Aggregate(
                short_name="Fixture",
                torrents={"ungrouped": torrent_models(HASH_A, HASH_B)},
            )
        )
        self.qbit = StubQbittorrentClient({HASH_C})
        self.service = AggregateTorrentService(self.repository, self.qbit)

    def test_update_moves_adds_ungroups_and_removes_torrents(self) -> None:
        grouped = self.service.update_aggregate_torrents(
            short_name="Fixture",
            group="Group A",
            add_hashes=[HASH_A, HASH_C],
        )
        self.assertEqual(
            grouped,
            {"ungrouped": [HASH_B], "Group A": [HASH_A, HASH_C]},
        )
        self.assertEqual(self.qbit.login_count, 1)
        self.assertEqual(self.qbit.requested_hashes, [HASH_C])

        ungrouped = self.service.update_aggregate_torrents(
            short_name="Fixture",
            add_hashes=[HASH_A],
        )
        self.assertEqual(
            ungrouped,
            {"ungrouped": [HASH_A, HASH_B], "Group A": [HASH_C]},
        )

        removed = self.service.update_aggregate_torrents(
            short_name="Fixture",
            remove_hashes=[HASH_B, HASH_C],
        )
        self.assertEqual(removed, {"ungrouped": [HASH_A]})

        with self.repository.get_repository(write=False) as repo:
            aggregate = repo.get_by_short_name("Fixture")
        self.assertIsNotNone(aggregate)
        self.assertEqual(aggregate.torrent_hashes_by_group(), removed)

    def test_qbittorrent_validation_failure_does_not_write(self) -> None:
        before = self.repository.get_by_short_name("Fixture")

        with self.assertRaisesRegex(ValueError, f"not found.*{HASH_D}"):
            self.service.update_aggregate_torrents(
                short_name="Fixture",
                group="Group A",
                add_hashes=[HASH_D],
            )

        after = self.repository.get_by_short_name("Fixture")
        self.assertEqual(after, before)

    def test_qbittorrent_validation_batches_and_reports_missing(self) -> None:
        self.qbit.available_hashes = {HASH_C}

        with self.assertRaisesRegex(
            ValueError,
            f"Torrents not found in qBittorrent: {HASH_A}, {HASH_D}",
        ):
            self.service.validate_torrent_hashes_in_qbittorrent(
                [HASH_A, HASH_C, HASH_D]
            )

        self.assertEqual(self.qbit.login_count, 1)
        self.assertEqual(self.qbit.requested_hashes, [HASH_A, HASH_C, HASH_D])


def aggregate_fixture() -> Aggregate:
    return Aggregate(
        short_name="Fixture",
        category="anime",
        bangumi_subjects=[
            BangumiSubject(
                subject_id=123,
                last_updated_at="2026-08-02T00:00:00",
                snapshot=BangumiSubjectSnapshot(
                    name="Fixture Subject",
                    name_cn="Fixture Subject CN",
                    type=2,
                    tags=[BangumiTag(name="fixture", count=1)],
                ),
            )
        ],
        torrents={
            "ungrouped": torrent_models(HASH_A),
            "Group A": torrent_models(HASH_B, HASH_C),
        },
    )


def torrent_models(*torrent_hashes: str) -> list[Torrent]:
    return [Torrent(hash=torrent_hash) for torrent_hash in torrent_hashes]


if __name__ == "__main__":
    unittest.main()
