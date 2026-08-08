from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import override

from config import SearchConfig
from lib.models.search import AggregateSearchDocument
from lib.search.repositories import LanceDbSearchRepository


class LanceDbSearchRepositoryTest(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str]
    search_path: Path
    repository: LanceDbSearchRepository

    @override
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="bonsai-search-repository-test-"
        )
        self.search_path = Path(self.temp_dir.name) / "search.lancedb"
        self.repository = LanceDbSearchRepository(
            SearchConfig(
                lancedb_path=self.search_path,
                embedding_model="test-model",
                embedding_query_prompt_model_marker="test-prompt-model",
                embedding_device="cpu",
            )
        )

    @override
    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_missing_store_is_not_created_by_readiness_check(self) -> None:
        self.assertFalse(self.repository.documents_table_exists())
        self.assertFalse(self.search_path.exists())

    def test_empty_store_is_not_changed_by_readiness_check(self) -> None:
        self.search_path.mkdir()

        self.assertFalse(self.repository.documents_table_exists())
        self.assertEqual(list(self.search_path.iterdir()), [])

    def test_repository_created_documents_table_is_ready(self) -> None:
        self.repository.replace_documents(
            [
                AggregateSearchDocument(
                    aggregate_short_name="Fixture",
                    source_text="short_name: Fixture",
                    source_hash="sha256:fixture",
                    embedding=[0.0, 1.0],
                    model_name="test-model",
                    updated_at="2026-08-08T00:00:00+00:00",
                )
            ]
        )

        self.assertTrue(self.repository.documents_table_exists())


if __name__ == "__main__":
    unittest.main()
