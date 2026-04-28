"""CLI app definition module.

Purpose: Defines the Typer app instance shared by all CLI command modules.
         Also provides the deferred import helper for monkeypatch compatibility.
Related: cli.py, _cli_trading.py, _cli_research.py, _cli_status.py.
"""

import typer

app = typer.Typer(add_completion=False, help="Moomoo bot CLI")


def cli_module():
    """Deferred import to avoid circular import during CLI module initialization."""
    import moomoo_bot.cli as _cli

    return _cli
