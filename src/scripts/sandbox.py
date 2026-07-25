from __future__ import annotations

import socket

import click


def socketpair_write_is_blocked() -> bool:
    try:
        read_socket, write_socket = socket.socketpair()
    except OSError:
        return False
    try:
        try:
            write_socket.send(b"x")
        except PermissionError:
            return True
        return False
    finally:
        read_socket.close()
        write_socket.close()


def warn_if_sandboxed(script_label: str) -> None:
    if socketpair_write_is_blocked():
        click.echo(
            "Warning: this environment blocks socketpair writes. asyncio/AnyIO MCP "
            f"stdio clients may hang here; run this {script_label} outside the "
            "sandbox if it times out.",
            err=True,
        )
