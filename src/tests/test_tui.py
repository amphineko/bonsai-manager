from __future__ import annotations

import asyncio
import unittest
from typing import override

from textual.containers import Container
from textual.widgets import DataTable, Input

from commands.tui import BonsaiTUI
from lib.models.aggregates import Aggregate, Torrent
from lib.models.search import AggregateSearchResult


class FakeTuiAggregateService:
    def __init__(
        self,
        *,
        warm_up_error: RuntimeError | None = None,
        search_error: RuntimeError | None = None,
        search_results: list[AggregateSearchResult] | None = None,
    ) -> None:
        self.aggregates = [
            Aggregate(short_name="Alpha", category="anime"),
            Aggregate(short_name="Beta", category="music"),
        ]
        self.search_error = search_error
        self.search_results = search_results
        self.warm_up_error = warm_up_error
        self.warm_up_calls = 0
        self.search_calls: list[tuple[str, int, float | None]] = []
        self.closed = False

    def list_aggregates(self) -> list[Aggregate]:
        return self.aggregates

    def search_aggregates(
        self,
        query: str,
        limit: int = 10,
        threshold: float | None = None,
    ) -> list[AggregateSearchResult]:
        self.search_calls.append((query, limit, threshold))
        if self.search_error is not None:
            raise self.search_error
        return self.search_results or [
            AggregateSearchResult(aggregate=self.aggregates[1], score=0.875)
        ]

    def warm_up_search(self) -> None:
        self.warm_up_calls += 1
        if self.warm_up_error is not None:
            raise self.warm_up_error

    def get_torrent_display_path(self, torrent: Torrent) -> str:
        return torrent.hash

    def close(self) -> None:
        self.closed = True


class SynchronousBonsaiTUI(BonsaiTUI):
    """Exercise TUI state without leaving Textual executor threads in unittest."""

    @override
    def start_semantic_search(self, query: str) -> None:
        self.search_timer = None
        if self.semantic_warm_up_error is not None:
            self.handle_semantic_search_error(query, self.semantic_warm_up_error)
            return
        try:
            results = self.manager.search_aggregates(query, limit=50)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            self.handle_semantic_search_error(query, exc)
            return
        self.apply_semantic_results(query, results)


class BonsaiTuiTest(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        asyncio.get_running_loop().slow_callback_duration = 1.0

    async def test_hybrid_search_shows_semantic_results_and_scores(self) -> None:
        manager = FakeTuiAggregateService()
        app = SynchronousBonsaiTUI(manager=manager)

        async with app.run_test(size=(120, 30)) as pilot:
            search_input = app.query_one("#search_input", Input)
            search_input.value = "space adventure"
            await pilot.pause(0.1)
            self.assertTrue(search_input.has_focus)
            await pilot.pause(0.5)

            self.assertEqual(manager.warm_up_calls, 1)
            self.assertEqual(manager.search_calls, [("space adventure", 50, None)])
            self.assertEqual(
                [aggregate.short_name for aggregate in app.filtered_entries],
                ["Beta"],
            )
            self.assertEqual(app.result_scores, {"Beta": 0.875})
            self.assertEqual(app.query_one(DataTable).row_count, 1)
            self.assertTrue(search_input.has_focus)

        self.assertTrue(manager.closed)

    async def test_search_container_is_auto_height_and_input_starts_focused(
        self,
    ) -> None:
        app = SynchronousBonsaiTUI(manager=FakeTuiAggregateService())

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            search_container = app.query_one("#search_container", Container)
            search_input = app.query_one("#search_input", Input)
            container_height = search_container.styles.height

            self.assertIsNotNone(container_height)
            self.assertTrue(container_height.is_auto)
            self.assertTrue(search_input.has_focus)

    async def test_filter_results_appear_before_semantic_results(self) -> None:
        manager = FakeTuiAggregateService()
        app = SynchronousBonsaiTUI(manager=manager)

        async with app.run_test(size=(120, 30)) as pilot:
            search_input = app.query_one("#search_input", Input)
            search_input.value = "^Al"
            await pilot.pause(0.1)

            self.assertEqual(manager.search_calls, [])
            self.assertEqual(
                [aggregate.short_name for aggregate in app.filtered_entries],
                ["Alpha"],
            )
            self.assertEqual(app.result_scores, {})

            await pilot.pause(0.5)
            self.assertEqual(manager.search_calls, [("^Al", 50, None)])
            self.assertEqual(
                [aggregate.short_name for aggregate in app.filtered_entries],
                ["Alpha", "Beta"],
            )
            self.assertEqual(app.result_scores, {"Beta": 0.875})

    async def test_hybrid_results_are_deduplicated_and_keep_semantic_score(
        self,
    ) -> None:
        manager = FakeTuiAggregateService()
        manager.search_results = [
            AggregateSearchResult(aggregate=manager.aggregates[0], score=0.75),
            AggregateSearchResult(aggregate=manager.aggregates[1], score=0.5),
        ]
        app = SynchronousBonsaiTUI(manager=manager)

        async with app.run_test(size=(120, 30)) as pilot:
            app.query_one("#search_input", Input).value = "^Al"
            await pilot.pause(0.5)

            self.assertEqual(
                [aggregate.short_name for aggregate in app.filtered_entries],
                ["Alpha", "Beta"],
            )
            self.assertEqual(app.result_scores, {"Alpha": 0.75, "Beta": 0.5})

    async def test_invalid_regex_falls_back_to_substring_filter(self) -> None:
        manager = FakeTuiAggregateService()
        manager.aggregates.append(Aggregate(short_name="[Special]", category="anime"))
        app = SynchronousBonsaiTUI(manager=manager)

        async with app.run_test(size=(120, 30)) as pilot:
            app.query_one("#search_input", Input).value = "["
            await pilot.pause(0.1)

            self.assertEqual(
                [aggregate.short_name for aggregate in app.filtered_entries],
                ["[Special]"],
            )

    async def test_semantic_search_failure_keeps_input_focused(self) -> None:
        manager = FakeTuiAggregateService(
            search_error=RuntimeError("index unavailable")
        )
        app = SynchronousBonsaiTUI(manager=manager)

        async with app.run_test(size=(120, 30)) as pilot:
            search_input = app.query_one("#search_input", Input)
            search_input.value = "Alpha"
            await pilot.pause(0.5)

            self.assertTrue(search_input.has_focus)
            self.assertEqual(
                [aggregate.short_name for aggregate in app.filtered_entries],
                ["Alpha"],
            )
            self.assertEqual(app.query_one(DataTable).row_count, 1)

    async def test_warm_up_failure_keeps_local_filter_results(self) -> None:
        manager = FakeTuiAggregateService(
            warm_up_error=RuntimeError("model unavailable")
        )
        app = SynchronousBonsaiTUI(manager=manager)

        async with app.run_test(size=(120, 30)) as pilot:
            search_input = app.query_one("#search_input", Input)
            search_input.value = "Alpha"
            await pilot.pause(0.5)
            self.assertEqual(
                [aggregate.short_name for aggregate in app.filtered_entries],
                ["Alpha"],
            )
            self.assertEqual(manager.search_calls, [])


if __name__ == "__main__":
    unittest.main()
