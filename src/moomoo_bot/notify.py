"""Notification module.

Purpose: Send trading alerts via webhook (Discord/Slack/Generic).
Related: orchestrator.py, state.py.
"""

from __future__ import annotations

import json
import logging
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)


def send_webhook(url: str, payload: dict) -> bool:
    """Send a JSON payload to a webhook URL. Returns True on success."""
    if not url:
        return False
    try:
        data = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (URLError, OSError, TimeoutError) as exc:
        logger.warning("Webhook delivery failed: %s", exc)
        return False


def notify_rebalance(
    url: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    reason: str,
    mode_label: str = "paper",
) -> None:
    """Notify about a rebalance order."""
    send_webhook(
        url,
        {
            "event": "rebalance",
            "mode": mode_label,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "estimated_value": quantity * price,
            "reason": reason,
        },
    )


def notify_risk_stop(
    url: str,
    reason: str,
    account_value: float,
    peak_value: float,
    drawdown_pct: float,
) -> None:
    """Notify about a risk stop event."""
    send_webhook(
        url,
        {
            "event": "risk_stop",
            "reason": reason,
            "account_value": account_value,
            "peak_value": peak_value,
            "drawdown_pct": round(drawdown_pct, 4),
        },
    )


def notify_error(url: str, error_message: str, consecutive_failures: int = 0) -> None:
    """Notify about an operational error."""
    send_webhook(
        url,
        {
            "event": "error",
            "message": error_message,
            "consecutive_failures": consecutive_failures,
        },
    )


def notify_daily_summary(
    url: str,
    account_value: float,
    day_return_pct: float,
    total_return_pct: float,
    drawdown_pct: float,
    positions: dict[str, float],
    halted: bool,
) -> None:
    """Notify with a daily performance summary."""
    send_webhook(
        url,
        {
            "event": "daily_summary",
            "account_value": account_value,
            "day_return_pct": round(day_return_pct, 4),
            "total_return_pct": round(total_return_pct, 4),
            "drawdown_pct": round(drawdown_pct, 4),
            "positions": positions,
            "halted": halted,
        },
    )


def notify_kill_switch(url: str) -> None:
    """Notify that the kill switch was triggered."""
    send_webhook(
        url,
        {
            "event": "kill_switch",
            "message": "Kill switch file detected. Trading halted.",
        },
    )


def notify_daily_limit(url: str, loss_pct: float, account_value: float) -> None:
    """Notify that the daily loss limit was hit."""
    send_webhook(
        url,
        {
            "event": "daily_loss_limit",
            "loss_pct": round(loss_pct, 4),
            "account_value": account_value,
            "message": f"Daily loss limit breached: {loss_pct:.2%}. All positions liquidated.",
        },
    )
