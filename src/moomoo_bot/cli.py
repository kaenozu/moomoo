"""CLI command module.

Purpose: Re-exports app and command functions for backward compatibility.
         Tests monkeypatch names on this module (get_settings, StateStore, etc.).
Related: _cli_app.py, _cli_trading.py, _cli_research.py, _cli_status.py.
"""

from __future__ import annotations

from moomoo_bot._cli_app import app  # noqa: F401
from moomoo_bot.config import get_settings, describe_runtime_profile_drift  # noqa: F401
from moomoo_bot.state import StateStore, resolve_state_db_path  # noqa: F401
from moomoo_bot.cli_helpers import parse_symbols as _parse_symbols  # noqa: F401
from moomoo_bot.cli_render import render_execution_report  # noqa: F401

from moomoo_bot._cli_trading import (  # noqa: F401
    _require_paper_mode,
    _require_live_mode,
    paper_run,
    paper_trade,
    paper_repair,
    live_trade,
    auto_run,
    autopilot,
)
from moomoo_bot._cli_research import (  # noqa: F401
    backtest,
    verify_api,
    research,
    satellite,
    validate,
)
from moomoo_bot._cli_status import (  # noqa: F401
    status,
    execution_report,
    performance,
)

if __name__ == "__main__":
    app()
