"""Compatibility wrapper for CLI commands.

Purpose: Preserve the old import path while the real CLI lives in cli.py.
Related: cli.py.
"""

__all__ = ["app"]

from moomoo_bot.cli import app
