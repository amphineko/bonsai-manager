from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Protocol, override

import click
from rich.console import Group
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import DataTable, Footer, Header, Input, Static

from config import Config, load_config
from lib.models.aggregates import Aggregate, Torrent
from lib.models.search import AggregateSearchResult
from lib.services import IndexedAggregateService

if TYPE_CHECKING:
    from textual.timer import Timer

SEMANTIC_SEARCH_DELAY_SECONDS = 0.3
SEMANTIC_SEARCH_LIMIT = 50


class TuiAggregateService(Protocol):
    def list_aggregates(self) -> list[Aggregate]: ...

    def search_aggregates(
        self,
        query: str,
        limit: int = 10,
        threshold: float | None = None,
    ) -> list[AggregateSearchResult]: ...

    def warm_up_search(self) -> None: ...

    def get_torrent_display_path(self, torrent: Torrent) -> str: ...

    def close(self) -> None: ...


def format_bangumi_name_cn(entry: Aggregate) -> str:
    return "\n".join(
        subject.snapshot.name_cn
        for subject in entry.bangumi_subjects
        if subject.snapshot
    )


def format_bangumi_subject_id(entry: Aggregate) -> str:
    if not entry.bangumi_subjects:
        return "-"
    return "\n".join(str(subject.subject_id) for subject in entry.bangumi_subjects)


def get_last_synced(entry: Aggregate) -> str:
    if not entry.bangumi_subjects:
        return "-"
    return max(subject.last_updated_at for subject in entry.bangumi_subjects)[:19]


class BonsaiTUI(App[None]):
    """A Textual TUI for Bonsai Manager."""

    TITLE = "Bonsai Browser"

    CSS = """
    Screen {
        background: #1e1e1e;
    }

    #search_container {
        height: auto;
        margin: 1 1;
        padding: 1;
        layout: horizontal;
        align: left middle;
    }

    #search_input {
        width: 1fr;
    }

    #main_container {
        layout: horizontal;
    }

    #table_container {
        width: auto;
        max-width: 50%;
        border: solid #333;
    }

    #detail_container {
        width: 1fr;
        border: solid #333;
        padding: 1;
        background: #252525;
    }

    #entry_table {
        height: 100%;
        width: auto;
    }

    .detail_title {
        text-style: bold;
        color: cyan;
        margin-bottom: 1;
    }

    .detail_label {
        color: green;
        text-style: bold;
    }

    .detail_text {
        margin-bottom: 1;
    }

    .torrent_path {
        color: yellow;
        text-style: italic;
    }
    """

    BINDINGS: ClassVar = [
        Binding("q", "quit", "Quit", show=True),
        Binding("escape", "clear_search", "Clear Search", show=True),
        Binding("f5", "refresh_db", "Refresh Data", show=True),
    ]

    def __init__(
        self,
        config: Config | None = None,
        manager: TuiAggregateService | None = None,
    ) -> None:
        super().__init__()
        self.config = config or load_config()
        self.manager = (
            manager
            if manager is not None
            else IndexedAggregateService.from_config(self.config)
        )
        self.semantic_warm_up_error: Exception | None = None
        try:
            self.manager.warm_up_search()
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            self.semantic_warm_up_error = exc
        self.all_entries: list[Aggregate] = []
        self.filtered_entries: list[Aggregate] = []
        self.result_scores: dict[str, float] = {}
        self.search_timer: Timer | None = None

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Input(
                placeholder="Search aggregates...",
                id="search_input",
            ),
            id="search_container",
        )
        yield Container(
            Container(DataTable(id="entry_table"), id="table_container"),
            Container(
                Static("Select an entry to see details", id="detail_view"),
                id="detail_container",
            ),
            id="main_container",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        # Using fixed widths for small columns, and flexible for name
        table.add_columns(
            "Score",
            "Category",
            "Short Name",
            "Chinese Name",
            "ID",
            "Torrents",
        )
        self.refresh_data()
        self.query_one("#search_input", Input).focus()

    def on_unmount(self) -> None:
        if self.search_timer is not None:
            self.search_timer.stop()
        self.manager.close()

    def refresh_data(self) -> None:
        self.all_entries = self.manager.list_aggregates()
        current_input = self.query_one(Input).value
        self.apply_search(current_input)

    def apply_search(self, query: str) -> None:
        if self.search_timer is not None:
            self.search_timer.stop()
            self.search_timer = None
        if not query:
            self.filtered_entries = self.all_entries
            self.result_scores = {}
            self.update_table()
        else:
            self.filtered_entries = self.filter_entries(query)
            self.result_scores = {}
            self.update_table()
            self.search_timer = self.set_timer(
                SEMANTIC_SEARCH_DELAY_SECONDS,
                lambda: self.start_semantic_search(query),
            )

    def start_semantic_search(self, query: str) -> None:
        self.search_timer = None
        if self.query_one(Input).value != query:
            return
        if self.semantic_warm_up_error is not None:
            self.handle_semantic_search_error(query, self.semantic_warm_up_error)
            return
        self.search_semantically(query)

    @work(
        group="semantic-search",
        exclusive=True,
        thread=True,
        exit_on_error=False,
    )
    def search_semantically(self, query: str) -> None:
        try:
            results = self.manager.search_aggregates(
                query,
                limit=SEMANTIC_SEARCH_LIMIT,
            )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            self.call_from_thread(self.handle_semantic_search_error, query, exc)
            return
        self.call_from_thread(self.apply_semantic_results, query, results)

    def apply_semantic_results(
        self,
        query: str,
        results: list[AggregateSearchResult],
    ) -> None:
        if self.query_one(Input).value != query:
            return
        local_entries = self.filter_entries(query)
        seen_short_names = {entry.short_name for entry in local_entries}
        semantic_entries = []
        for result in results:
            if result.aggregate.short_name in seen_short_names:
                continue
            semantic_entries.append(result.aggregate)
            seen_short_names.add(result.aggregate.short_name)
        self.filtered_entries = local_entries + semantic_entries
        self.result_scores = {
            result.aggregate.short_name: result.score for result in results
        }
        self.update_table()

    def handle_semantic_search_error(self, query: str, error: Exception) -> None:
        if self.query_one(Input).value != query:
            return
        self.filtered_entries = self.filter_entries(query)
        self.result_scores = {}
        self.update_table()
        self.notify(
            f"Semantic search failed: {error}",
            severity="error",
        )

    def filter_entries(self, filter_str: str) -> list[Aggregate]:
        if not filter_str:
            return self.all_entries
        try:
            pattern = re.compile(filter_str, re.IGNORECASE)
            return [
                entry
                for entry in self.all_entries
                if pattern.search(entry.short_name)
                or pattern.search(format_bangumi_name_cn(entry))
                or pattern.search(entry.category)
            ]
        except re.error:
            filter_str_lower = filter_str.lower()
            return [
                entry
                for entry in self.all_entries
                if filter_str_lower in entry.short_name.lower()
                or filter_str_lower in format_bangumi_name_cn(entry).lower()
                or filter_str_lower in entry.category.lower()
            ]

    def update_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for entry in self.filtered_entries:
            # Text objects bypass DataTable markup parsing entirely.
            table.add_row(
                Text(
                    f"{self.result_scores[entry.short_name]:.3f}"
                    if entry.short_name in self.result_scores
                    else "-"
                ),
                Text(entry.category),
                Text(entry.short_name),
                Text(format_bangumi_name_cn(entry) or "-"),
                Text(format_bangumi_subject_id(entry)),
                Text(str(entry.torrent_count)),
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search_input":
            self.apply_search(event.value)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.cursor_row is not None and event.cursor_row < len(
            self.filtered_entries
        ):
            entry = self.filtered_entries[event.cursor_row]
            self.show_detail(entry)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.cursor_row is not None and event.cursor_row < len(
            self.filtered_entries
        ):
            entry = self.filtered_entries[event.cursor_row]
            self.show_detail(entry)

    def show_detail(self, entry: Aggregate) -> None:
        detail_view = self.query_one("#detail_view", Static)

        # Build rich renderable group to avoid markup parsing issues
        renderables = []

        title_line = Text()
        title_line.append(entry.short_name, style="bold cyan")
        title_line.append(f" [{entry.category}]", style="magenta")
        renderables.append(title_line)

        if entry.bangumi_subjects:
            renderables.append(Text("Bangumi Subjects:", style="bold"))
            for subject in entry.bangumi_subjects:
                if subject.snapshot:
                    renderables.append(Text(subject.snapshot.name_cn, style="green"))
                    renderables.append(Text(f"Original: {subject.snapshot.name}"))
                    renderables.append(Text(f"Type: {subject.snapshot.type or '-'}"))
                    if subject.snapshot.tags:
                        renderables.append(
                            Text(
                                "Tags: "
                                + ", ".join(tag.name for tag in subject.snapshot.tags)
                            )
                        )
                else:
                    renderables.append(Text("Snapshot: missing", style="yellow"))
                renderables.append(Text(f"Bangumi ID: {subject.subject_id}"))
                renderables.append(
                    Text(f"Last Updated: {subject.last_updated_at[:19]}")
                )
                renderables.append(Text(""))
        else:
            renderables.append(Text("Bangumi: unmapped", style="yellow"))
        renderables.append(Text(""))

        renderables.append(Text("Torrents:", style="bold"))
        for group_name, torrents in entry.torrents.items():
            renderables.append(Text(group_name, style="bold yellow"))
            for torrent in torrents:
                display_path = self.manager.get_torrent_display_path(torrent)
                torrent_line = Text("• ")
                torrent_line.append(display_path, style="yellow")
                renderables.append(torrent_line)
            renderables.append(Text(""))

        detail_view.update(Group(*renderables))

    def action_clear_search(self) -> None:
        self.query_one(Input).value = ""
        self.apply_search("")

    def action_refresh_db(self) -> None:
        self.refresh_data()
        self.notify("Database reloaded")


if __name__ == "__main__":
    app = BonsaiTUI()
    app.run()


@click.command(name="tui")
@click.pass_obj
def launch_tui(config: Config) -> None:
    """Launch the interactive TUI with semantic and regex search."""
    app = BonsaiTUI(config)
    app.run()
