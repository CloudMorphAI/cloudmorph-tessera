"""Tessera CLI — stub for Task 1 verification; full implementation in Task 13."""

from __future__ import annotations

import typer

app = typer.Typer(name="tessera", help="Tessera MCP firewall.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Show version information."""
    from tessera import __version__

    typer.echo(f"tessera {__version__}")


if __name__ == "__main__":
    app()
