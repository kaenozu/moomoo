"""CLI app definition module.

Purpose: Defines the Typer app instance shared by all CLI command modules.
Related: cli.py, _cli_trading.py, _cli_research.py, _cli_status.py.
"""

import typer

app = typer.Typer(add_completion=False, help="Moomoo bot CLI")
