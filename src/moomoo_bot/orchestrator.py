"""Orchestration module.

Purpose: Business logic for running trading operations.
Related: cli.py, paper.py, risk.py, strategy modules.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
from time import sleep

from moomoo import Session, TrdEnv

from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.broker.paper import MoomooPaperTradeClient
from moomoo_bot.cli_helpers import (
    build_monthly_strategy as _build_monthly_strategy,
    fetch_market_state as _fetch_market_state,
    is_regular_market_open as _is_regular_market_open,
    position_quantities as _position_quantities,
    submit_orders_with_duplicate_guard as _submit_orders_with_duplicate_guard,
    trade_mode_label as _trade_mode_label,
)
from moomoo_bot.cli_render import console, render_order_response, render_paper_plan, render_paper_trade_plan, render_risk_orders
from moomoo_bot.money import convert_capital_to_usd
from moomoo_bot.paper import PaperPlan, build_paper_plan, build_paper_rebalance_orders
from moomoo_bot.strategy.base import Strategy
from moomoo_bot.risk import (
    RiskState,
    build_liquidation_orders,
    build_stop_loss_take_profit_orders,
    detect_market_shock,
    update_drawdown_state,
)

logger = logging.getLogger(__name__)


def _round_order_price(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _resolve_order_prices(
    quote_client: MoomooOpenDClient,
    symbol_universe: list[str],
    fallback_prices: dict[str, float],
) -> dict[str, float]:
    try:
        snapshot = quote_client.fetch_market_snapshot(symbol_universe)
    except Exception as exc:
        logger.warning("Falling back to historical close prices after snapshot fetch failed: %s", exc)
        return fallback_prices

    if snapshot.empty:
        return fallback_prices

    order_prices = dict(fallback_prices)
    for _, row in snapshot.iterrows():
        code = str(row.get("code", "")).strip()
        if not code:
            continue
        try:
            last_price = float(row.get("last_price", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if last_price > 0.0:
            order_prices[code] = _round_order_price(last_price)
    return order_prices


def _overlay_latest_prices(price_frame, latest_prices: dict[str, float]):
    adjusted_frame = price_frame.copy()
    if adjusted_frame.empty:
        return adjusted_frame
    last_index = adjusted_frame.index[-1]
    for symbol, price in latest_prices.items():
        if symbol in adjusted_frame.columns:
            adjusted_frame.loc[last_index, symbol] = price
    return adjusted_frame


def _reprice_orders(instructions, latest_prices: dict[str, float]):
    repriced = []
    for instruction in instructions:
        repriced.append(
            replace(
                instruction,
                price=_round_order_price(latest_prices.get(instruction.symbol, instruction.price)),
            )
        )
    return repriced


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
) -> None:
    """Execute a single trading decision and optionally submit orders."""
    selected_symbols = symbols
    benchmark_label = benchmark_symbol
    paper_capital = capital if capital is not None else settings.initial_capital
    resolved_fx_rate = fx_jpy_per_usd if fx_jpy_per_usd is not None else settings.fx_jpy_per_usd
    paper_capital_usd = convert_capital_to_usd(paper_capital, settings.capital_currency, resolved_fx_rate)

    owns_quote_client = quote_client is None
    owns_trade_client = trade_client is None
    if owns_quote_client:
        quote_client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    if owns_trade_client:
        try:
            trade_client = MoomooPaperTradeClient(host=settings.opend_host, port=settings.opend_port, trd_env=trade_env)
        except Exception:
            if owns_quote_client:
                quote_client.close()
            raise
    mode_label = _trade_mode_label(trade_env)
    try:
        position_frame = trade_client.get_position_frame()
        current_positions = _position_quantities(position_frame)
        symbol_universe = list(dict.fromkeys([*selected_symbols, *current_positions.keys()]))
        price_frame, benchmark_series = quote_client.fetch_price_panel(symbol_universe, benchmark_label, history_days=history_days)
        historical_latest_prices = {symbol: float(price_frame.iloc[-1][symbol]) for symbol in price_frame.columns}
        latest_prices = _resolve_order_prices(quote_client, symbol_universe, historical_latest_prices)
        order_price_frame = _overlay_latest_prices(price_frame, latest_prices)
        market_state = _fetch_market_state(quote_client, benchmark_label)
        market_open = _is_regular_market_open(market_state)
        account_value = trade_client.get_account_value() if capital is None else paper_capital_usd
        risk_state = RiskState()
        shock_reason = detect_market_shock(benchmark_series, settings.market_shock_drop_pct)
        if shock_reason:
            logger.warning(f"Risk stop: {shock_reason}")
            console.print(f"Risk stop: {shock_reason}")
            render_risk_orders([], current_positions, "Risk Stop Orders")
            return

        drawdown_reason = update_drawdown_state(account_value, risk_state, settings.max_drawdown_pct, settings.max_drawdown_reset_pct)
        if drawdown_reason:
            liquidation_orders = build_liquidation_orders(current_positions, latest_prices, drawdown_reason)
            render_paper_plan(
                PaperPlan(as_of=price_frame.index[-1], capital=account_value, reason=drawdown_reason, allocations=[], cash_remaining=account_value),
                benchmark_label,
                ", ".join(symbol_universe),
                benchmark_series,
            )
            if capital is None:
                console.print(f"Capital input: {account_value:,.2f} USD (from {mode_label} account)")
            else:
                console.print(f"Capital input: {capital:,.2f} {settings.capital_currency}")
                console.print(f"Capital used for sizing: {account_value:,.2f} USD")
            render_risk_orders(liquidation_orders, current_positions, "Risk Stop Orders")
            console.print(f"Submitting {mode_label} risk stop liquidation orders...")
            _submit_orders_with_duplicate_guard(trade_client, liquidation_orders, mode_label, render_order_response)
            return

        strategy = strategy or _build_monthly_strategy(settings)
        decision = strategy.decide(price_frame, price_frame.index[-1])
        plan = build_paper_plan(
            order_price_frame,
            decision,
            account_value,
            minimum_order_value=minimum_order_value,
            max_position_weight=max_position_weight,
        )
        instructions = build_paper_rebalance_orders(plan, current_positions=current_positions, latest_prices=latest_prices, market_open=market_open)
        risk_orders = build_stop_loss_take_profit_orders(position_frame, latest_prices, settings.stop_loss_pct, settings.take_profit_pct)
        instructions = _reprice_orders(instructions, latest_prices)
        risk_orders = _reprice_orders(risk_orders, latest_prices)

        render_paper_trade_plan(plan, benchmark_label, ", ".join(symbol_universe), benchmark_series, current_positions, instructions)
        if capital is None:
            console.print(f"Capital input: {account_value:,.2f} USD (from {mode_label} account)")
        else:
            console.print(f"Capital input: {capital:,.2f} {settings.capital_currency}")
            console.print(f"Capital used for sizing: {account_value:,.2f} USD")
        if not market_open:
            console.print(f"Market state: {market_state}; buy orders will use ETH session.")

        if risk_orders:
            render_risk_orders(risk_orders, current_positions, "Risk Exit Orders")
            console.print(f"Submitting {mode_label} risk exit orders...")
            _submit_orders_with_duplicate_guard(trade_client, risk_orders, mode_label, render_order_response)

        if instructions:
            console.print(f"Submitting {mode_label} orders...")
            _submit_orders_with_duplicate_guard(trade_client, instructions, mode_label, render_order_response)
        elif not risk_orders:
            console.print("No paper orders were required.")
    finally:
        if owns_trade_client:
            trade_client.close()
        if owns_quote_client:
            quote_client.close()


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
    quote_client: MoomooOpenDClient | None = None,
    trade_client: MoomooPaperTradeClient | None = None,
    strategy: Strategy | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> None:
    """Run continuous monitoring loop for paper trading."""
    selected_symbols = symbols
    benchmark_label = benchmark_symbol
    paper_capital_input = capital if capital is not None else settings.initial_capital
    resolved_fx_rate = fx_jpy_per_usd if fx_jpy_per_usd is not None else settings.fx_jpy_per_usd
    paper_capital_usd = convert_capital_to_usd(paper_capital_input, settings.capital_currency, resolved_fx_rate)
    resolved_max_position_weight = (
        max_position_weight if max_position_weight is not None else settings.max_single_position_weight
    )

    owns_quote_client = quote_client is None
    owns_trade_client = trade_client is None
    if owns_quote_client:
        quote_client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    if owns_trade_client:
        try:
            trade_client = MoomooPaperTradeClient(host=settings.opend_host, port=settings.opend_port)
        except Exception:
            if owns_quote_client:
                quote_client.close()
            raise
    strategy = strategy or _build_monthly_strategy(settings)
    sleep_fn = sleep_fn or sleep
    risk_state = RiskState()

    console.print(
        f"Starting auto-run monitor for {', '.join(selected_symbols)} vs {benchmark_label}; "
        f"polling every {poll_seconds} seconds."
    )
    console.print(f"Capital input: {paper_capital_input:,.2f} {settings.capital_currency}")
    console.print(f"Capital used for sizing: {paper_capital_usd:,.2f} USD")

    try:
        consecutive_failures = 0
        while True:
            try:
                price_frame, benchmark_series = quote_client.fetch_price_panel(selected_symbols, benchmark_label, history_days=history_days)
                position_frame = trade_client.get_position_frame()
                current_positions = _position_quantities(position_frame)
                symbol_universe = list(dict.fromkeys([*selected_symbols, *current_positions.keys()]))
                historical_latest_prices = {symbol: float(price_frame.iloc[-1][symbol]) for symbol in price_frame.columns}
                latest_prices = _resolve_order_prices(quote_client, symbol_universe, historical_latest_prices)
                order_price_frame = _overlay_latest_prices(price_frame, latest_prices)
                account_value = trade_client.get_account_value()
                market_state = _fetch_market_state(quote_client, benchmark_label)
                market_open = _is_regular_market_open(market_state)

                shock_reason = detect_market_shock(benchmark_series, settings.market_shock_drop_pct)
                if shock_reason:
                    logger.warning(f"Risk stop active - {shock_reason}")
                    console.print(f"{price_frame.index[-1].date()}: risk stop active - {shock_reason}")
                    sleep_fn(poll_seconds)
                    continue

                drawdown_reason = update_drawdown_state(account_value, risk_state, settings.max_drawdown_pct, settings.max_drawdown_reset_pct)
                if risk_state.halted:
                    if drawdown_reason:
                        liquidation_orders = build_liquidation_orders(
                            current_positions,
                            latest_prices,
                            drawdown_reason,
                            session=Session.NONE if market_open else Session.ETH,
                            fill_outside_rth=not market_open,
                        )
                        render_risk_orders(liquidation_orders, current_positions, "Risk Stop Orders")
                        if liquidation_orders:
                            console.print("Submitting risk stop liquidation orders...")
                            _submit_orders_with_duplicate_guard(trade_client, liquidation_orders, "paper", render_order_response)
                        logger.warning(f"Trading halted - {drawdown_reason}")
                        console.print(f"{price_frame.index[-1].date()}: trading halted - {drawdown_reason}")
                    else:
                        risk_state.peak_account_value = account_value
                        console.print(f"{price_frame.index[-1].date()}: drawdown recovered, resuming normal operations")
                    sleep_fn(poll_seconds)
                    continue

                decision = strategy.decide(price_frame, price_frame.index[-1])
                plan = build_paper_plan(
                    order_price_frame,
                    decision,
                    paper_capital_usd,
                    minimum_order_value=minimum_order_value,
                    max_position_weight=resolved_max_position_weight,
                )
                instructions = build_paper_rebalance_orders(
                    plan,
                    current_positions=current_positions,
                    latest_prices=latest_prices,
                    market_open=market_open,
                )
                risk_orders = build_stop_loss_take_profit_orders(
                    position_frame,
                    latest_prices,
                    settings.stop_loss_pct,
                    settings.take_profit_pct,
                    session=Session.NONE if market_open else Session.ETH,
                    fill_outside_rth=not market_open,
                )
                instructions = _reprice_orders(instructions, latest_prices)
                risk_orders = _reprice_orders(risk_orders, latest_prices)

                render_paper_trade_plan(plan, benchmark_label, ", ".join(symbol_universe), benchmark_series, current_positions, instructions)
                console.print(f"Capital input: {paper_capital_input:,.2f} {settings.capital_currency}")
                console.print(f"Capital used for sizing: {plan.capital:,.2f} USD")
                if not market_open:
                    console.print(f"Market state: {market_state}; buy orders will use ETH session.")

                if risk_orders:
                    render_risk_orders(risk_orders, current_positions, "Risk Exit Orders")
                    console.print("Submitting risk exit orders...")
                    _submit_orders_with_duplicate_guard(trade_client, risk_orders, "paper", render_order_response)

                if instructions:
                    console.print("Submitting paper orders...")
                    _submit_orders_with_duplicate_guard(trade_client, instructions, "paper", render_order_response)
                else:
                    console.print(f"{price_frame.index[-1].date()}: no rebalance required; monitoring only.")

                consecutive_failures = 0
                sleep_fn(poll_seconds)
            except Exception as exc:
                consecutive_failures += 1
                wait_seconds = min(poll_seconds * (2 ** (consecutive_failures - 1)), poll_seconds * 8)
                logger.exception(f"auto-run cycle failed ({consecutive_failures}/{max_consecutive_failures}): {exc}")
                console.print(f"auto-run cycle failed ({consecutive_failures}/{max_consecutive_failures}): {exc}")
                if consecutive_failures >= max_consecutive_failures:
                    logger.error("auto-run stopped after repeated failures.")
                    console.print("auto-run stopped after repeated failures.")
                    break
                sleep_fn(wait_seconds)
    finally:
        if owns_trade_client:
            trade_client.close()
        if owns_quote_client:
            quote_client.close()

