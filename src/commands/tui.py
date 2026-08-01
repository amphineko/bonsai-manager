from __future__ import annotations

import re
from typing import ClassVar, override

import click
from rich.console import Group
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import DataTable, Footer, Header, Input, Static

from config import Config, load_config
from lib.models.aggregates import Aggregate
from lib.services import AggregateService


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
        height: 3;
        margin: 1 1;
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

    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config = config or load_config()
        self.manager = AggregateService(self.config)
        self.all_entries: list[Aggregate] = []
        self.filtered_entries: list[Aggregate] = []

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Input(
                placeholder="Search (regex supported, including category)...",
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
        table.add_columns("Category", "Short Name", "Chinese Name", "ID", "Torrents")
        self.refresh_data()

    def refresh_data(self) -> None:
        self.all_entries = self.manager.list_aggregates()
        current_input = self.query_one(Input).value
        self.apply_filter(current_input)

    def apply_filter(self, filter_str: str) -> None:
        if not filter_str:
            self.filtered_entries = self.all_entries
        else:
            try:
                pattern = re.compile(filter_str, re.IGNORECASE)
                self.filtered_entries = [
                    e
                    for e in self.all_entries
                    if pattern.search(e.short_name)
                    or pattern.search(format_bangumi_name_cn(e))
                    or pattern.search(e.category)
                ]
            except re.error:
                filter_str_lower = filter_str.lower()
                self.filtered_entries = [
                    e
                    for e in self.all_entries
                    if filter_str_lower in e.short_name.lower()
                    or filter_str_lower in format_bangumi_name_cn(e).lower()
                    or filter_str_lower in e.category.lower()
                ]
        self.update_table()

    def update_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for entry in self.filtered_entries:
            # Text objects bypass DataTable markup parsing entirely.
            table.add_row(
                Text(entry.category),
                Text(entry.short_name),
                Text(format_bangumi_name_cn(entry) or "-"),
                Text(format_bangumi_subject_id(entry)),
                Text(str(entry.torrent_count)),
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search_input":
            self.apply_filter(event.value)

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
        self.apply_filter("")

    def action_refresh_db(self) -> None:
        self.refresh_data()
        self.notify("Database reloaded")


if __name__ == "__main__":
    app = BonsaiTUI()
    app.run()


@click.command(name="tui")
@click.pass_obj
def launch_tui(config: Config) -> None:
    """Launch the interactive TUI."""
    app = BonsaiTUI(config)
    app.run()
