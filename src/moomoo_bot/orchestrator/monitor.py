"""Auto monitor module.

Purpose: Continuous monitoring loop for automated paper trading.
Related: orchestrator/__init__.py, orchestrator/cycle.py.
"""

from __future__ import annotations

import json as _json
import logging
from collections.abc import Callable
from time import sleep

from moomoo import TrdEnv

from moomoo_bot.cli_helpers import trade_mode_label as _trade_mode_label
from moomoo_bot.cli_render import console
from moomoo_bot.kill_switch import is_kill_switch_active as _is_kill_switch_active
from moomoo_bot.notify import (
    notify_daily_summary,
    notify_error,
    notify_kill_switch,
)
from moomoo_bot.health import HealthCheckServer
from moomoo_bot.state import StateStore
from moomoo_bot.strategy.base import Strategy

from moomoo_bot.orchestrator.helpers import (
    kill_switch_message,
    webhook_str,
)

logger = logging.getLogger(__name__)


def run_auto_monitor(
    *,
    settings,
    symbols: list[str],
    benchmark_symbol: str,
    history_days: int,
    capital: float | None,
    fx_jpy_per_usd: float | None,
    minimum_order_value: float,
    poll_seconds: int,
    max_consecutive_failures: int,
    max_position_weight: float | None = None,
    quote_client=None,
    trade_client=None,
    strategy: Strategy | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    state_store: StateStore | None = None,
) -> None:
    from moomoo_bot.orchestrator.cycle import execute_trading_cycle

    sleep_fn = sleep_fn or sleep
    mode_label = _trade_mode_label(TrdEnv.SIMULATE)
    webhook_url = webhook_str(settings)
    console.print(
        f"Starting auto-run monitor for {', '.join(symbols)} vs {benchmark_symbol}; "
        f"polling every {poll_seconds} seconds."
    )

    health_server: HealthCheckServer | None = None
    if settings.health_check_enabled:
        health_server = HealthCheckServer(port=settings.health_check_port)
        health_server.start()
        logger.info("Health check server started on port %d", settings.health_check_port)

    owns_state_store = state_store is None
    if owns_state_store:
        state_store = StateStore(
            db_path=settings.state_db_path,
            execution_mode=settings.execution_mode,
        )

    if _is_kill_switch_active():
        message = kill_switch_message()
        logger.warning(message)
        console.print(message)
        notify_kill_switch(webhook_url)
        if owns_state_store:
            state_store.close()
        if health_server is not None:
            health_server.stop()
        return

    consecutive_failures = 0
    last_summary_date: str | None = None
    trade_count = 0

    try:
        while True:
            try:
                if _is_kill_switch_active():
                    message = kill_switch_message()
                    logger.warning(message)
                    console.print(message)
                    notify_kill_switch(webhook_url)
                    break

                result = execute_trading_cycle(
                    settings=settings,
                    trade_env=TrdEnv.SIMULATE,
                    symbols=symbols,
                    benchmark_symbol=benchmark_symbol,
                    history_days=history_days,
                    capital=capital,
                    fx_jpy_per_usd=fx_jpy_per_usd,
                    minimum_order_value=minimum_order_value,
                    max_position_weight=max_position_weight,
                    quote_client=quote_client,
                    trade_client=trade_client,
                    strategy=strategy,
                    state_store=state_store,
                    submit_orders=True,
                    auto_mode=True,
                    mode_label=mode_label,
                )
                if not result:
                    break

                consecutive_failures = 0

                latest_equity = state_store.get_latest_equity_before_market_date(
                    "9999-12-31"
                )
                current_date = (
                    latest_equity.market_date if latest_equity is not None else None
                )
                current_value = (
                    latest_equity.account_value if latest_equity is not None else None
                )

                if current_date is not None and last_summary_date is not None and current_date != last_summary_date:
                    prev_equity = state_store.get_latest_equity_before_market_date(current_date)
                    prev_value = prev_equity.account_value if prev_equity else current_value
                    if current_value is not None and prev_value is not None and prev_value > 0:
                        peak_state = state_store.load_risk_state()
                        peak = peak_state.peak_account_value or current_value
                        day_return = (current_value - prev_value) / prev_value
                        month_start_equity = state_store.get_equity_at_month_start(current_date)
                        initial_value = (
                            month_start_equity.account_value
                            if month_start_equity is not None
                            else prev_value
                        )
                        total_return = (
                            (current_value - initial_value) / initial_value
                            if initial_value and initial_value > 0
                            else 0.0
                        )
                        drawdown = (peak - current_value) / peak if peak > 0 else 0.0
                        try:
                            positions = _json.loads(latest_equity.positions_json or "{}")
                        except Exception:
                            positions = {}
                        notify_daily_summary(
                            webhook_url,
                            account_value=current_value,
                            day_return_pct=day_return,
                            total_return_pct=total_return,
                            drawdown_pct=drawdown,
                            positions=positions,
                            halted=peak_state.halted,
                        )

                if current_date is not None:
                    last_summary_date = current_date
                trade_count += 1

                if health_server is not None:
                    health_server.update_status(
                        is_healthy=True,
                        account_value=current_value,
                        risk_halted=False,
                        trade_count=trade_count,
                    )

                sleep_fn(poll_seconds)
            except Exception as exc:
                consecutive_failures += 1
                wait_seconds = min(
                    poll_seconds * (2 ** (consecutive_failures - 1)), poll_seconds * 8
                )
                logger.exception(
                    f"auto-run cycle failed ({consecutive_failures}/{max_consecutive_failures}): {exc}"
                )
                console.print(
                    f"auto-run cycle failed ({consecutive_failures}/{max_consecutive_failures}): {exc}"
                )
                notify_error(webhook_url, str(exc), consecutive_failures)
                if health_server is not None:
                    health_server.update_status(
                        is_healthy=False,
                        last_error=str(exc),
                        trade_count=trade_count,
                    )
                if consecutive_failures >= max_consecutive_failures:
                    logger.error("auto-run stopped after repeated failures.")
                    console.print("auto-run stopped after repeated failures.")
                    notify_error(
                        webhook_url,
                        f"auto-run stopped after {consecutive_failures} consecutive failures.",
                        consecutive_failures,
                    )
                    break
                sleep_fn(wait_seconds)
    finally:
        if health_server is not None:
            health_server.stop()
        if owns_state_store:
            state_store.close()
