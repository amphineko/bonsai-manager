import shlex

import click
from textual_serve.server import Server

from config import Config, PROJECT_ROOT


def build_tui_command() -> str:
    return " ".join(
        [
            "uv",
            "--directory",
            shlex.quote(str(PROJECT_ROOT)),
            "run",
            "python",
            "src/main.py",
            "tui",
        ]
    )


def parse_listen(listen: str) -> tuple[str, int]:
    if "://" in listen:
        raise click.BadParameter("Use HOST:PORT, not a URL", param_hint="--listen")
    if ":" not in listen:
        raise click.BadParameter("Expected HOST:PORT", param_hint="--listen")

    host, port_text = listen.rsplit(":", 1)
    if not host:
        raise click.BadParameter("Host cannot be empty", param_hint="--listen")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise click.BadParameter(
            "Port must be an integer", param_hint="--listen"
        ) from exc
    if not 1 <= port <= 65535:
        raise click.BadParameter(
            "Port must be between 1 and 65535", param_hint="--listen"
        )
    return host, port


@click.command(name="serve")
@click.option(
    "--listen",
    default=None,
    show_default=True,
    metavar="HOST:PORT",
    help="Host and port for the local web server.",
)
@click.option(
    "--public-url",
    help="Public URL to advertise when serving behind a proxy.",
)
@click.option(
    "--title",
    default=None,
    show_default=True,
    help="Browser page title.",
)
@click.option("--devtools", is_flag=True, help="Enable Textual devtools.")
@click.pass_obj
def serve(
    config: Config,
    listen: str | None,
    public_url: str | None,
    title: str | None,
    devtools: bool,
):
    """Serve the Bonsai TUI as a local web app."""
    host, port = parse_listen(listen or config.web.listen)
    server = Server(
        build_tui_command(),
        host=host,
        port=port,
        title=title or config.web.title,
        public_url=public_url,
    )
    server.serve(debug=devtools)
