"""Notification module.

Purpose: Send trading alerts via webhook (Discord/Slack/Generic).
Related: orchestrator.py, state.py.
"""

from __future__ import annotations

import json
import logging
import re
from time import sleep
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError

from moomoo_bot.retry import with_retries, TRANSIENT_EXCEPTIONS, DEFAULT_BASE_DELAY

logger = logging.getLogger(__name__)

# 允许的スキーム: https のみ (セキュリティ)
_ALLOWED_SCHEMES = {"https"}
# 危険なホストパターン: localhost, プライベートIP範囲等をブロック
_FORBIDDEN_HOST_PATTERNS = [
    re.compile(r'^localhost$', re.IGNORECASE),
    re.compile(r'^127\.0\.0\.1$'),
    re.compile(r'^10\.\d+\.\d+\.\d+$'),
    re.compile(r'^172\.(1[6-9]|2[0-9]|3[0-1])\.\d+\.\d+$'),
    re.compile(r'^192\.168\.\d+\.\d+$'),
]


def send_webhook(url: str, payload: dict) -> bool:
    """Send a JSON payload to a webhook URL. Returns True on success."""
    if not url:
        return False

    # URL 構造检査
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        logger.warning("Webhook URL must use HTTPS: %s", url)
        return False
    if not parsed.netloc:
        logger.warning("Webhook URL invalid (missing host): %s", url)
        return False

    # ローカル/プライベートホストのブロック
    hostname = parsed.hostname or ""
    for pattern in _FORBIDDEN_HOST_PATTERNS:
        if pattern.match(hostname):
            logger.warning("Webhook URL points to disallowed host %s: %s", hostname, url)
            return False

    try:
        return _do_send_webhook(url, payload)
    except Exception as exc:
        logger.warning("Webhook delivery failed: %s", exc)
        return False


@with_retries(
    max_retries=2,  # 3 attempts: 1 initial + 2 retries
    base_delay=1.0,
    backoff_factor=1.0,  # Fixed 1s delay
    exceptions=(URLError, OSError, TimeoutError),
    raise_on_failure=RuntimeError,
)
def _do_send_webhook(url: str, payload: dict) -> bool:
    """Actual webhook send with retry. Returns True on 2xx response."""
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=10) as resp:
        status = resp.status
        return 200 <= status < 300


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
