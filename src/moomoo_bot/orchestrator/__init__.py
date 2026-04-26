"""Orchestration package.

Purpose: Business logic for running trading operations.
         Re-exports public API and monkeypatched names for backward compatibility.
Related: orchestrator/helpers.py, orchestrator/cycle.py, orchestrator/repair.py.
"""

from __future__ import annotations

from moomoo import TrdEnv

from moomoo_bot.cli_helpers import (  # noqa: F401
    submit_orders_with_duplicate_guard as _submit_orders_with_duplicate_guard,
    trade_mode_label as _trade_mode_label,
)
from moomoo_bot.cli_render import (  # noqa: F401
    console as console,
    render_order_response as render_order_response,
    render_paper_trade_plan as render_paper_trade_plan,
    render_risk_orders as render_risk_orders,
)
from moomoo_bot.kill_switch import is_kill_switch_active as _is_kill_switch_active  # noqa: F401
from moomoo_bot.paper import build_paper_plan as build_paper_plan  # noqa: F401
from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.broker.paper import MoomooPaperTradeClient
from moomoo_bot.state import StateStore
from moomoo_bot.strategy.base import Strategy

from moomoo_bot.orchestrator.helpers import (  # noqa: F401
    cleanup_equity_history as _cleanup_equity_history,
    daily_order_cap_reason as _daily_order_cap_reason,
    daily_loss_reference as _daily_loss_reference,
    effective_max_position_weight as _effective_max_position_weight,
    is_daily_loss_halt as _is_daily_loss_halt,
    kill_switch_message as _kill_switch_message,
    market_date_for_frame as _market_date_for_frame,
    webhook_str as _webhook_str,
)
from moomoo_bot.orchestrator.cycle import (  # noqa: F401
    broker_row_matches_order as _broker_row_matches_order,
    execute_trading_cycle as _execute_trading_cycle,
    reconcile_pending_orders as _reconcile_pending_orders,
)
from moomoo_bot.orchestrator.repair import run_paper_repair as run_paper_repair  # noqa: F401
from moomoo_bot.orchestrator.monitor import run_auto_monitor as run_auto_monitor  # noqa: F401


def run_one_shot_trade(
    *,
    settings,
    trade_env: TrdEnv,
    symbols: list[str],
    benchmark_symbol: str,
    history_days: int,
    capital: float | None,
    fx_jpy_per_usd: float | None,
    minimum_order_value: float,
    max_position_weight: float = 1.0,
    quote_client: MoomooOpenDClient | None = None,
    trade_client: MoomooPaperTradeClient | None = None,
    strategy: Strategy | None = None,
    state_store: StateStore | None = None,
    submit_orders: bool = True,
) -> None:
    _execute_trading_cycle(
        settings=settings,
        trade_env=trade_env,
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
        submit_orders=submit_orders,
        auto_mode=False,
        mode_label=_trade_mode_label(trade_env),
    )
