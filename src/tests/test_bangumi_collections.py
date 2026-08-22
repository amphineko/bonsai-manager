from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import override
from unittest.mock import Mock, call, patch

from config import BangumiConfig, Config
from lib.bangumi import BangumiClient
from lib.http_client import DEFAULT_HTTP_TIMEOUT
from lib.models.aggregates import Aggregate, Torrent
from lib.models.bangumi import (
    BangumiCollectionAggregateCoverage,
    BangumiCollectionLocalState,
    BangumiCollectionSubjectCoverage,
    BangumiCollectionType,
    BangumiRemoteCollection,
    BangumiSubject,
    BangumiSubjectSnapshot,
)
from lib.models.sync import BangumiCollectionSyncResult, SyncStepStatus
from lib.services.bangumi_collections import BangumiCollectionService
from lib.sql.bangumi_collections import SqliteBangumiCollectionRepository
from lib.sql.repositories import SqliteAggregateRepository

SYNCED_AT = datetime(2026, 8, 3, 12, tzinfo=UTC)


class Clock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class StubBangumiClient(BangumiClient):
    def __init__(self, collections: list[BangumiRemoteCollection]) -> None:
        self.collections = collections
        self.collection_calls: list[str] = []

    @override
    def close(self) -> None:
        pass

    @override
    def get_user_collections(
        self,
        username: str,
    ) -> list[BangumiRemoteCollection]:
        self.collection_calls.append(username)
        return [collection.model_copy(deep=True) for collection in self.collections]


class BangumiCollectionServiceTest(unittest.TestCase):
    repository: SqliteAggregateRepository
    clock: Clock

    @override
    def setUp(self) -> None:
        self.repository = SqliteAggregateRepository(":memory:")
        self.clock = Clock(SYNCED_AT)

    @override
    def tearDown(self) -> None:
        self.repository.close()

    def test_unconfigured_collection_sync_is_skipped(self) -> None:
        client = StubBangumiClient([])
        service = self.service(client, username=None)

        result = service.sync()

        self.assertEqual(
            result,
            BangumiCollectionSyncResult(
                status=SyncStepStatus.SKIPPED,
                reason="BANGUMI_USERNAME is not configured.",
            ),
        )
        self.assertEqual(client.collection_calls, [])

    def test_sync_respects_ttl_and_force_for_empty_collection(self) -> None:
        client = StubBangumiClient([])
        service = self.service(client)

        first = service.sync()
        self.clock.current += timedelta(hours=1)
        fresh = service.sync()
        forced = service.sync(force=True)

        self.assertEqual(first.status, SyncStepStatus.COMPLETED)
        self.assertEqual(first.fetched, 0)
        self.assertEqual(
            fresh,
            BangumiCollectionSyncResult(
                status=SyncStepStatus.SKIPPED,
                reason="Bangumi collection mirror is still fresh.",
                last_successful_sync_at=SYNCED_AT,
                next_refresh_at=SYNCED_AT + timedelta(hours=6),
            ),
        )
        self.assertEqual(forced.status, SyncStepStatus.COMPLETED)
        self.assertEqual(client.collection_calls, ["fixture", "fixture"])

    def test_sync_tracks_all_states_removal_and_reactivation(self) -> None:
        client = StubBangumiClient(
            [
                collection_fixture(subject_id, collection_type)
                for subject_id, collection_type in enumerate(
                    BangumiCollectionType,
                    start=1,
                )
            ]
        )
        service = self.service(client)

        created = service.sync()
        client.collections = [
            collection_fixture(1, BangumiCollectionType.DOING),
            collection_fixture(2, BangumiCollectionType.DONE),
        ]
        self.clock.current += timedelta(hours=7)
        removed = service.sync()
        client.collections = [
            collection_fixture(1, BangumiCollectionType.DOING),
            collection_fixture(2, BangumiCollectionType.DONE),
            collection_fixture(3, BangumiCollectionType.DOING),
        ]
        self.clock.current += timedelta(hours=7)
        reactivated = service.sync()

        self.assertEqual((created.fetched, created.created), (5, 5))
        self.assertEqual((removed.removed, removed.updated), (3, 1))
        self.assertEqual((removed.unchanged, removed.reactivated), (1, 0))
        self.assertEqual((reactivated.reactivated, reactivated.unchanged), (1, 2))

        with self.repository.get_repository(write=False) as aggregate_repository:
            collection_repository = SqliteBangumiCollectionRepository(
                aggregate_repository.require_session()
            )
            rows = collection_repository.list_for_user("fixture")
        self.assertIsNone(rows[2].removed_at)
        self.assertIsNotNone(rows[3].removed_at)
        self.assertIsNotNone(rows[4].removed_at)

    def test_failed_write_preserves_mirror_and_expiry(self) -> None:
        client = StubBangumiClient([collection_fixture(1, BangumiCollectionType.DOING)])
        service = self.service(client)
        service.sync()
        client.collections = [
            BangumiRemoteCollection(
                subject_id=2,
                subject_type=2,
                type=BangumiCollectionType.DOING,
                updated_at=SYNCED_AT,
                subject=None,
            )
        ]
        self.clock.current += timedelta(hours=7)

        with self.assertRaisesRegex(ValueError, "has no snapshot"):
            service.sync()

        with self.repository.get_repository(write=False) as aggregate_repository:
            collection_repository = SqliteBangumiCollectionRepository(
                aggregate_repository.require_session()
            )
            rows = collection_repository.list_for_user("fixture")
            state = collection_repository.get_sync_state("fixture")
        self.assertEqual([row.subject_id for row in rows], [1])
        self.assertIsNotNone(state)
        self.assertEqual(state.last_successful_sync_at, SYNCED_AT)

    def test_aggregate_import_preserves_collection_subjects(self) -> None:
        client = StubBangumiClient([collection_fixture(1, BangumiCollectionType.DOING)])
        self.service(client).sync()

        with self.repository.get_repository(write=True) as aggregate_repository:
            aggregate_repository.import_all([])

        with self.repository.get_repository(write=False) as aggregate_repository:
            collection_repository = SqliteBangumiCollectionRepository(
                aggregate_repository.require_session()
            )
            rows = collection_repository.list_for_user("fixture")
        self.assertEqual([row.subject_id for row in rows], [1])

    def test_subject_coverage_supports_default_and_composed_filters(self) -> None:
        client = StubBangumiClient(
            [
                collection_fixture(1, BangumiCollectionType.WISH),
                collection_fixture(2, BangumiCollectionType.DOING),
                collection_fixture(3, BangumiCollectionType.DOING),
                collection_fixture(4, BangumiCollectionType.ON_HOLD),
                collection_fixture(5, BangumiCollectionType.DROPPED),
            ]
        )
        service = self.service(client)
        service.sync()
        with self.repository.get_repository(write=True) as repository:
            repository.add(coverage_aggregate(2, "Empty"))
            repository.add(coverage_aggregate(3, "Available", "a" * 40))
            repository.add(coverage_aggregate(4, "On hold", "b" * 40))
            repository.add(coverage_aggregate(5, "Dropped empty"))

        self.assertEqual(
            service.list_subject_coverage(),
            [
                coverage_result(
                    1,
                    BangumiCollectionType.WISH,
                    BangumiCollectionLocalState.UNMAPPED,
                ),
                coverage_result(
                    2,
                    BangumiCollectionType.DOING,
                    BangumiCollectionLocalState.EMPTY,
                    aggregate_name="Empty",
                ),
            ],
        )
        self.assertEqual(
            service.list_subject_coverage(
                [BangumiCollectionType.ON_HOLD, BangumiCollectionType.DROPPED],
                [BangumiCollectionLocalState.WITH_TORRENTS],
            ),
            [
                coverage_result(
                    4,
                    BangumiCollectionType.ON_HOLD,
                    BangumiCollectionLocalState.WITH_TORRENTS,
                    aggregate_name="On hold",
                    torrent_count=1,
                )
            ],
        )

    def service(
        self,
        client: StubBangumiClient,
        *,
        username: str | None = "fixture",
    ) -> BangumiCollectionService:
        return BangumiCollectionService(
            self.repository,
            client,
            BangumiConfig(
                base_url="https://example.invalid",
                user_agent="test",
                token=None,
                username=username,
                collection_ttl=timedelta(hours=6),
            ),
            now=self.clock,
        )


class BangumiClientCollectionTest(unittest.TestCase):
    client: BangumiClient

    @override
    def setUp(self) -> None:
        self.client = BangumiClient(bangumi_config())

    @override
    def tearDown(self) -> None:
        self.client.close()

    def test_collection_pagination_uses_returned_total(self) -> None:
        responses = [
            response_mock(collection_page(51, 0, range(1, 51))),
            response_mock(collection_page(51, 50, [51])),
        ]

        with patch.object(self.client.session, "get", side_effect=responses) as get:
            collections = self.client.get_user_collections("fixture")

        self.assertEqual(len(collections), 51)
        self.assertEqual(
            get.call_args_list,
            [
                call(
                    "https://example.invalid/v0/users/fixture/collections",
                    params={"subject_type": 2, "limit": 50, "offset": 0},
                    timeout=DEFAULT_HTTP_TIMEOUT,
                ),
                call(
                    "https://example.invalid/v0/users/fixture/collections",
                    params={"subject_type": 2, "limit": 50, "offset": 50},
                    timeout=DEFAULT_HTTP_TIMEOUT,
                ),
            ],
        )

    def test_collection_pagination_rejects_incomplete_page(self) -> None:
        response = response_mock(collection_page(1, 0, []))

        with (
            patch.object(self.client.session, "get", return_value=response),
            self.assertRaisesRegex(ValueError, "ended before total"),
        ):
            self.client.get_user_collections("fixture")

    def test_collection_fetches_missing_subject_snapshot(self) -> None:
        collection_response = response_mock(
            {
                "total": 1,
                "limit": 50,
                "offset": 0,
                "data": [remote_collection_payload(1, include_subject=False)],
            }
        )
        subject_response = response_mock(
            {"name": "Fallback", "name_cn": "回退", "type": 2, "tags": []}
        )

        with patch.object(
            self.client.session,
            "get",
            side_effect=[collection_response, subject_response],
        ):
            collections = self.client.get_user_collections("fixture")

        snapshot = collections[0].subject
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.name, "Fallback")


class BangumiConfigTest(unittest.TestCase):
    def test_collection_defaults_and_username_normalization(self) -> None:
        config = Config.from_env({"BANGUMI_USERNAME": "  fixture  "})

        self.assertEqual(config.bangumi.username, "fixture")
        self.assertEqual(config.bangumi.collection_ttl, timedelta(hours=6))
        self.assertEqual(
            config.audit_bangumi_collection_types,
            (BangumiCollectionType.WISH, BangumiCollectionType.DOING),
        )
        self.assertEqual(
            config.audit_bangumi_collection_local_states,
            (
                BangumiCollectionLocalState.UNMAPPED,
                BangumiCollectionLocalState.EMPTY,
            ),
        )

    def test_collection_audit_filters_parse_names(self) -> None:
        config = Config.from_env(
            {
                "AUDIT_BANGUMI_COLLECTION_TYPES": "on_hold,dropped",
                "AUDIT_BANGUMI_COLLECTION_LOCAL_STATES": "with_torrents",
            }
        )

        self.assertEqual(
            config.audit_bangumi_collection_types,
            (BangumiCollectionType.ON_HOLD, BangumiCollectionType.DROPPED),
        )
        self.assertEqual(
            config.audit_bangumi_collection_local_states,
            (BangumiCollectionLocalState.WITH_TORRENTS,),
        )

    def test_collection_ttl_rejects_negative_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            Config.from_env({"BANGUMI_COLLECTION_TTL_SECONDS": "-1"})


def collection_fixture(
    subject_id: int,
    collection_type: BangumiCollectionType,
) -> BangumiRemoteCollection:
    return BangumiRemoteCollection(
        subject_id=subject_id,
        subject_type=2,
        type=collection_type,
        updated_at=SYNCED_AT + timedelta(minutes=subject_id),
        subject=BangumiSubjectSnapshot(
            name=f"Subject {subject_id}",
            name_cn=f"条目 {subject_id}",
            type=2,
        ),
    )


def coverage_aggregate(
    subject_id: int,
    short_name: str,
    torrent_hash: str | None = None,
) -> Aggregate:
    return Aggregate(
        short_name=short_name,
        bangumi_subjects=[
            BangumiSubject(
                subject_id=subject_id,
                last_updated_at=SYNCED_AT.isoformat(),
                snapshot=subject_snapshot(subject_id),
            )
        ],
        torrents=(
            {"ungrouped": [Torrent(hash=torrent_hash)]}
            if torrent_hash is not None
            else {}
        ),
    )


def coverage_result(
    subject_id: int,
    collection_type: BangumiCollectionType,
    local_state: BangumiCollectionLocalState,
    *,
    aggregate_name: str | None = None,
    torrent_count: int = 0,
) -> BangumiCollectionSubjectCoverage:
    return BangumiCollectionSubjectCoverage(
        subject=BangumiSubject(
            subject_id=subject_id,
            last_updated_at=SYNCED_AT.isoformat(),
            snapshot=subject_snapshot(subject_id),
        ),
        collection_type=collection_type,
        local_state=local_state,
        aggregates=(
            [
                BangumiCollectionAggregateCoverage(
                    short_name=aggregate_name,
                    torrent_count=torrent_count,
                )
            ]
            if aggregate_name is not None
            else []
        ),
        torrent_count=torrent_count,
    )


def subject_snapshot(subject_id: int) -> BangumiSubjectSnapshot:
    return BangumiSubjectSnapshot(
        name=f"Subject {subject_id}",
        name_cn=f"条目 {subject_id}",
        type=2,
    )


def bangumi_config() -> BangumiConfig:
    return BangumiConfig(
        base_url="https://example.invalid",
        user_agent="test",
        token=None,
        username="fixture",
        collection_ttl=timedelta(hours=6),
    )


def collection_page(
    total: int,
    offset: int,
    subject_ids: range | list[int],
) -> dict[str, object]:
    return {
        "total": total,
        "limit": 50,
        "offset": offset,
        "data": [remote_collection_payload(subject_id) for subject_id in subject_ids],
    }


def remote_collection_payload(
    subject_id: int,
    *,
    include_subject: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "subject_id": subject_id,
        "subject_type": 2,
        "type": 3,
        "updated_at": SYNCED_AT.isoformat(),
    }
    if include_subject:
        payload["subject"] = {
            "name": f"Subject {subject_id}",
            "name_cn": "",
            "type": 2,
            "tags": list[object](),
        }
    return payload


def response_mock(payload: object) -> Mock:
    response = Mock()
    response.json.return_value = payload
    return response


if __name__ == "__main__":
    unittest.main()
