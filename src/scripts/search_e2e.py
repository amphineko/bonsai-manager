#!/usr/bin/env -S uv run python
from __future__ import annotations

import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, override

import click

from config import Config, SearchConfig, load_config
from lib.models.aggregates import Aggregate, Torrent
from lib.models.bangumi import BangumiSubject, BangumiSubjectSnapshot, BangumiTag
from lib.models.search import AggregateSearchDocument
from lib.search import AggregateSearchManager
from lib.search.repositories import LanceDbSearchRepository
from scripts.sandbox import warn_if_sandboxed

if TYPE_CHECKING:
    from lib.search.repositories import SearchRepository

logger = logging.getLogger(__name__)


class FakeSearchManager(AggregateSearchManager):
    def __init__(
        self,
        config: SearchConfig,
        aggregates: list[Aggregate],
        repository: SearchRepository | None = None,
    ):
        super().__init__(
            config=config,
            aggregates=StaticAggregateProvider(aggregates),
            repository=repository,
        )
        self.encode_call_count = 0
        self.encoded_text_count = 0

    @override
    def encode(
        self,
        texts: list[str],
        is_query: bool,
        show_progress: bool = False,
    ) -> list[list[float]]:
        self.encode_call_count += 1
        self.encoded_text_count += len(texts)
        return [fake_embedding(text) for text in texts]


class StaticAggregateProvider:
    def __init__(self, aggregates: list[Aggregate]):
        self.aggregates = aggregates

    def list_aggregates(self) -> list[Aggregate]:
        return self.aggregates

    def get_by_short_names(self, short_names: list[str]) -> list[Aggregate]:
        aggregate_by_short_name = {
            aggregate.short_name: aggregate for aggregate in self.aggregates
        }
        return [
            aggregate_by_short_name[short_name]
            for short_name in short_names
            if short_name in aggregate_by_short_name
        ]


def fake_embedding(text: str) -> list[float]:
    lower_text = text.lower()
    return [
        1.0 if "alpha" in lower_text else 0.0,
        1.0 if "beta" in lower_text else 0.0,
        1.0,
    ]


class BaseSearchE2ETest(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str]
    lancedb_path: Path
    config: SearchConfig

    @override
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="bonsai-search-e2e-")
        temp_path = Path(self.temp_dir.name)
        self.lancedb_path = temp_path / "aggregate_search.lancedb"
        self.config = SearchConfig(
            lancedb_path=self.lancedb_path,
            embedding_model="fake-e2e-model",
            embedding_query_prompt_model_marker="",
            embedding_device="cpu",
        )
        logger.info("prepared temporary search store: %s", temp_path)

    @override
    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        logger.info("cleaned up temporary search store")

    def aggregate_fixtures(self) -> list[Aggregate]:
        return [
            Aggregate(
                short_name="Alpha",
                category="anime",
                bangumi_subjects=[
                    BangumiSubject(
                        subject_id=1001,
                        last_updated_at="2026-07-19T00:00:00",
                        snapshot=BangumiSubjectSnapshot(
                            name="Alpha Subject",
                            name_cn="Alpha 中文名",
                            type=2,
                            tags=[BangumiTag(name="alpha", count=10)],
                        ),
                    )
                ],
                torrents=[Torrent(hash="a" * 40)],
            ),
            Aggregate(
                short_name="Beta",
                category="anime",
                bangumi_subjects=[
                    BangumiSubject(
                        subject_id=1002,
                        last_updated_at="2026-07-19T00:00:00",
                        snapshot=BangumiSubjectSnapshot(
                            name="Beta Subject",
                            name_cn="Beta 中文名",
                            type=2,
                            tags=[BangumiTag(name="beta", count=10)],
                        ),
                    )
                ],
                torrents=[Torrent(hash="b" * 40)],
            ),
        ]

    def assert_search_store_written(self) -> None:
        self.assertTrue(self.lancedb_path.is_dir())

    def assert_indexed_aggregates(
        self,
        manager: AggregateSearchManager,
        expected_short_names: list[str],
    ) -> None:
        self.assertEqual(
            [document.aggregate_short_name for document in manager.list_documents()],
            expected_short_names,
        )

    def assert_cached_queries(
        self,
        manager: AggregateSearchManager,
        expected_queries: list[str],
    ) -> None:
        query_cache = manager.load_query_cache()
        self.assertEqual(
            [entry.query for entry in query_cache.queries],
            expected_queries,
        )


class MockedSearchE2ETest(BaseSearchE2ETest):
    def test_fake_embedding_search_index_and_cache_flow(self) -> None:
        logger.info("test step: initialize deterministic LanceDB search index")
        aggregates = self.aggregate_fixtures()
        manager = FakeSearchManager(self.config, aggregates)
        manager.rebuild()

        logger.info("test step: run deterministic search")
        results = manager.search(
            "alpha",
            limit=2,
            threshold=0.75,
        )

        self.assertEqual([result.aggregate.short_name for result in results], ["Alpha"])
        self.assert_search_store_written()
        self.assertEqual(manager.encode_call_count, 2)
        self.assertEqual(manager.encoded_text_count, 3)

        logger.info(
            "test step: verify repeated search reuses document index and query cache"
        )
        second_results = manager.search(
            "alpha",
            limit=2,
            threshold=0.75,
        )

        self.assertEqual(
            [result.aggregate.short_name for result in second_results],
            ["Alpha"],
        )
        self.assertEqual(manager.encode_call_count, 2)
        self.assertEqual(manager.encoded_text_count, 3)

        logger.info("test step: force index refresh")
        manager.rebuild(
            force=True,
        )
        refreshed_results = manager.search(
            "alpha",
            limit=2,
            threshold=0.75,
        )

        self.assertEqual(
            [result.aggregate.short_name for result in refreshed_results],
            ["Alpha"],
        )
        self.assertEqual(manager.encode_call_count, 3)
        self.assertEqual(manager.encoded_text_count, 5)

        query_cache = manager.load_query_cache()
        self.assertEqual(query_cache.embedding_model, "fake-e2e-model")
        self.assert_indexed_aggregates(manager, ["Alpha", "Beta"])
        self.assert_cached_queries(manager, ["alpha"])

    def test_search_filters_embedding_model_before_limit(self) -> None:
        logger.info("test step: initialize current-model LanceDB search index")
        aggregates = self.aggregate_fixtures()
        manager = FakeSearchManager(self.config, aggregates)
        manager.rebuild()

        logger.info("test step: add nearer old-model row to shared table")
        old_config = replace(self.config, embedding_model="old-e2e-model")
        old_repository = LanceDbSearchRepository(old_config)
        old_repository.upsert_document(
            AggregateSearchDocument(
                aggregate_short_name="Old Model Result",
                source_text="gamma",
                source_hash=AggregateSearchDocument.source_hash_from_text("gamma"),
                embedding=fake_embedding("gamma"),
                model_name=old_config.embedding_model,
                updated_at="2026-07-19T00:00:00+00:00",
            )
        )

        logger.info("test step: search should ignore old-model row before limiting")
        results = manager.search(
            "gamma",
            limit=1,
            threshold=0.5,
        )

        self.assertEqual(len(results), 1)
        self.assertIn(results[0].aggregate.short_name, {"Alpha", "Beta"})

    def test_search_index_consistency_check(self) -> None:
        logger.info("test step: build a consistent search index")
        aggregates = self.aggregate_fixtures()
        manager = FakeSearchManager(self.config, aggregates)
        manager.rebuild()

        healthy_check = manager.check_consistency()
        self.assertTrue(healthy_check.healthy)
        self.assertEqual(healthy_check.aggregate_count, 2)
        self.assertEqual(healthy_check.document_count, 2)

        logger.info("test step: introduce missing, orphaned, and stale documents")
        manager.delete_aggregate("Alpha")
        manager.repository.upsert_document(
            AggregateSearchDocument(
                aggregate_short_name="Beta",
                source_text="stale beta",
                source_hash=AggregateSearchDocument.source_hash_from_text("stale beta"),
                embedding=fake_embedding("beta"),
                model_name=self.config.embedding_model,
                updated_at="2026-07-19T00:00:00+00:00",
            )
        )
        manager.repository.upsert_document(
            AggregateSearchDocument(
                aggregate_short_name="Orphan",
                source_text="orphan",
                source_hash=AggregateSearchDocument.source_hash_from_text("orphan"),
                embedding=fake_embedding("orphan"),
                model_name=self.config.embedding_model,
                updated_at="2026-07-19T00:00:00+00:00",
            )
        )

        unhealthy_check = manager.check_consistency()
        self.assertFalse(unhealthy_check.healthy)
        self.assertEqual(unhealthy_check.aggregate_count, 2)
        self.assertEqual(unhealthy_check.document_count, 2)
        self.assertEqual(unhealthy_check.missing_documents, ["Alpha"])
        self.assertEqual(unhealthy_check.orphaned_documents, ["Orphan"])
        self.assertEqual(unhealthy_check.stale_documents, ["Beta"])
        self.assertEqual(unhealthy_check.duplicate_documents, [])


class SearchE2ETest(BaseSearchE2ETest):
    allow_download = False
    device_override: str | None = None
    base_config: Config | None = None

    def test_real_embedding_generation(self) -> None:
        if self.base_config is None:
            raise AssertionError("base_config was not initialized.")

        logger.info("test step: run real embedding search smoke test")
        base_search_config = self.base_config.search
        search_config = replace(
            base_search_config,
            lancedb_path=self.lancedb_path,
            embedding_device=self.device_override
            or base_search_config.embedding_device,
        )
        aggregates = self.aggregate_fixtures()
        manager = AggregateSearchManager(
            config=search_config,
            aggregates=StaticAggregateProvider(aggregates),
            local_files_only=not self.allow_download,
        )
        manager.rebuild()
        results = manager.search("alpha subject", limit=2)

        self.assertTrue(results)
        self.assert_search_store_written()

        query_cache = manager.load_query_cache()
        documents = manager.list_documents()
        self.assertEqual(len(documents), len(aggregates))
        self.assertEqual(len(query_cache.queries), 1)
        dimensions = {len(document.embedding) for document in documents}
        dimensions.add(len(query_cache.queries[0].embedding))
        self.assertEqual(len(dimensions), 1)
        self.assertGreater(next(iter(dimensions)), 0)


@click.command("search-e2e")
@click.option(
    "--no-mock-embedding",
    is_flag=True,
    help="Load the configured embedding model instead of only using the fake encoder.",
)
@click.option(
    "--allow-download",
    is_flag=True,
    help="Allow downloading model files for --no-mock-embedding.",
)
@click.option("--device", default=None, help="Embedding device override.")
@click.pass_obj
def search_e2e(
    config: Config | None,
    no_mock_embedding: bool,
    allow_download: bool,
    device: str | None,
) -> None:
    """Run E2E smoke tests for semantic search indexing and embeddings."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    warn_if_sandboxed("Search E2E test")

    SearchE2ETest.allow_download = allow_download
    SearchE2ETest.device_override = device
    SearchE2ETest.base_config = config or load_config()

    suite = unittest.TestSuite()
    suite.addTests(
        unittest.defaultTestLoader.loadTestsFromTestCase(MockedSearchE2ETest)
    )
    if no_mock_embedding:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(SearchE2ETest))

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise click.exceptions.Exit(1)

    click.echo("Search E2E passed.")


if __name__ == "__main__":
    search_e2e()
