"""Kill switch module.

Purpose: File-based emergency stop for the trading bot.
Related: orchestrator.py, notify.py.
"""

from __future__ import annotations

from pathlib import Path

_KILL_SWITCH_FILE = Path.home() / ".moomoo_bot" / "KILL_SWITCH"


def is_kill_switch_active() -> bool:
    """Check if the kill switch file exists."""
    return _KILL_SWITCH_FILE.exists()


def activate_kill_switch() -> None:
    """Create the kill switch file."""
    _KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KILL_SWITCH_FILE.write_text("ACTIVE\n")


def deactivate_kill_switch() -> None:
    """Remove the kill switch file."""
    if _KILL_SWITCH_FILE.exists():
        _KILL_SWITCH_FILE.unlink()


def kill_switch_path() -> Path:
    """Return the kill switch file path."""
    return _KILL_SWITCH_FILE
