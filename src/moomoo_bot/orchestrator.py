"""Orchestration module.

Purpose: Business logic for running trading operations.
Related: cli.py, paper.py, risk.py, strategy modules.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from time import sleep
import pandas as pd

from moomoo import Session, TrdEnv

from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.broker.paper import MoomooPaperTradeClient
from moomoo_bot.cli_helpers import (
    build_monthly_strategy as _build_monthly_strategy,
    fetch_market_state as _fetch_market_state,
    is_regular_market_open as _is_regular_market_open,
    position_quantities as _position_quantities,
    requires_benchmark_prices as _requires_benchmark_prices,
    submit_orders_with_duplicate_guard as _submit_orders_with_duplicate_guard,
    trade_mode_label as _trade_mode_label,
)
from moomoo_bot.cli_render import (
    console,
    render_order_response,
    render_paper_plan,
    render_paper_trade_plan,
    render_risk_orders,
)
from moomoo_bot.kill_switch import (
    is_kill_switch_active as _is_kill_switch_active,
    kill_switch_path as _kill_switch_path,
)
from moomoo_bot.money import convert_capital_to_usd
from moomoo_bot.paper import PaperPlan, build_paper_plan, build_paper_rebalance_orders
from moomoo_bot.strategy.base import Strategy
from moomoo_bot.risk import (
    RiskState,
    build_liquidation_orders,
    build_stop_loss_take_profit_orders,
    detect_daily_loss_limit,
    detect_market_shock,
    update_drawdown_state,
)
from moomoo_bot.state import PersistentRiskState, StateStore

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
        logger.warning(
            "Falling back to historical close prices after snapshot fetch failed: %s",
            exc,
        )
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
        price_val = latest_prices.get(instruction.symbol, instruction.price)
        if price_val is None:
            price_val = instruction.price if instruction.price is not None else 0.0
        repriced.append(
            replace(
                instruction,
                price=_round_order_price(float(price_val)),
            )
        )
    return repriced


def _kill_switch_message() -> str:
    return f"Kill switch active at {_kill_switch_path()}; trading halted."


def _market_date_for_frame(price_frame) -> str:
    """Extract ISO date string from the last row of a price frame index."""
    try:
        last_index = price_frame.index[-1]
    except (IndexError, KeyError) as exc:
        raise ValueError("Price frame is empty; cannot determine market date") from exc

    try:
        ts = pd.Timestamp(last_index)
        if pd.isna(ts):
            raise ValueError("Price frame index is NaT; cannot determine market date")
        return ts.date().isoformat()
    except (ValueError, TypeError, AttributeError):
        try:
            ts = pd.to_datetime(last_index)
            if pd.isna(ts):
                raise ValueError(
                    "Price frame index converted to NaT; cannot determine market date"
                )
            return ts.date().isoformat()
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Failed to convert price frame index {last_index} to date"
            ) from exc


def _restore_risk_state(persistent_state: PersistentRiskState) -> RiskState:
    return RiskState(
        peak_account_value=persistent_state.peak_account_value,
        halted=persistent_state.halted,
        halted_reason=persistent_state.halted_reason,
        drawdown_tier=persistent_state.drawdown_tier,
    )


def _save_risk_state(
    state_store: StateStore,
    risk_state: RiskState,
    persistent_state: PersistentRiskState,
    market_date: str,
    account_value: float,
) -> None:
    state_store.save_risk_state(
        PersistentRiskState(
            peak_account_value=risk_state.peak_account_value,
            halted=risk_state.halted,
            halted_reason=risk_state.halted_reason,
            drawdown_tier=risk_state.drawdown_tier,
            daily_order_count=persistent_state.daily_order_count,
            daily_order_date=market_date,
            last_equity_value=account_value,
        )
    )


def _estimate_cash(
    account_value: float,
    positions: dict[str, float],
    latest_prices: dict[str, float],
) -> float:
    invested_value = sum(
        float(quantity) * float(latest_prices.get(symbol, 0.0))
        for symbol, quantity in positions.items()
    )
    return float(account_value - invested_value)


def _record_state_snapshot(
    state_store: StateStore,
    account_value: float,
    positions: dict[str, float],
    latest_prices: dict[str, float],
    market_date: str,
) -> None:
    state_store.record_equity(
        account_value=account_value,
        cash=_estimate_cash(account_value, positions, latest_prices),
        positions=positions,
        market_date=market_date,
    )
    state_store.record_positions(positions, latest_prices)


def _prepare_persistent_state_for_market_date(
    persistent_state: PersistentRiskState,
    market_date: str,
) -> None:
    if persistent_state.daily_order_date == market_date:
        return
    persistent_state.daily_order_count = 0
    persistent_state.daily_order_date = market_date


def _daily_order_cap_reason(
    persistent_state: PersistentRiskState,
    market_date: str,
    requested_order_count: int,
    max_daily_orders: int,
) -> str | None:
    if requested_order_count <= 0:
        return None
    current_count = (
        persistent_state.daily_order_count
        if persistent_state.daily_order_date == market_date
        else 0
    )
    projected_count = current_count + requested_order_count
    if projected_count <= max_daily_orders:
        return None
    return (
        f"daily order cap reached for {market_date}: current={current_count}, "
        f"requested={requested_order_count}, max={max_daily_orders}"
    )


def _record_submitted_order_count(
    state_store: StateStore,
    risk_state: RiskState,
    persistent_state: PersistentRiskState,
    market_date: str,
    account_value: float,
    submitted_order_count: int,
) -> None:
    if submitted_order_count <= 0:
        return
    _prepare_persistent_state_for_market_date(persistent_state, market_date)
    persistent_state.daily_order_count += submitted_order_count
    _save_risk_state(
        state_store,
        risk_state,
        persistent_state,
        market_date,
        account_value,
    )


def _daily_loss_reference(state_store: StateStore, market_date: str) -> float | None:
    snapshot = state_store.get_latest_equity_before_market_date(market_date)
    if snapshot is None:
        return None
    return float(snapshot.account_value)


def _is_daily_loss_halt(reason: str | None) -> bool:
    return bool(reason) and reason.startswith("daily_loss_limit:")


def _clear_expired_daily_loss_halt(
    risk_state: RiskState,
    persistent_state: PersistentRiskState,
    market_date: str,
) -> None:
    if not risk_state.halted or not _is_daily_loss_halt(risk_state.halted_reason):
        return
    if persistent_state.daily_order_date == market_date:
        return
    risk_state.halted = False
    risk_state.halted_reason = None
    risk_state.drawdown_tier = 0


def _build_risk_liquidation_orders(
    current_positions: dict[str, float],
    latest_prices: dict[str, float],
    reason: str,
    settings,
    market_open: bool,
):
    return build_liquidation_orders(
        current_positions,
        latest_prices,
        reason,
        session=Session.NONE if market_open else Session.ETH,
        fill_outside_rth=not market_open,
        fractional_share_precision=settings.fractional_share_precision,
    )


def _render_and_submit_risk_liquidation(
    trade_client,
    liquidation_orders,
    current_positions: dict[str, float],
    mode_label: str,
    submit_orders: bool = True,
    state_store: StateStore | None = None,
) -> None:
    render_risk_orders(liquidation_orders, current_positions, "Risk Stop Orders")
    if not submit_orders or not liquidation_orders:
        return
    console.print(f"Submitting {mode_label} risk stop liquidation orders...")
    _submit_orders_with_duplicate_guard(
        trade_client,
        liquidation_orders,
        mode_label,
        render_order_response,
        state_store=state_store,
    )


def _effective_max_position_weight(
    base_max_position_weight: float, risk_state: RiskState
) -> float:
    if risk_state.halted or risk_state.drawdown_tier < 1:
        return base_max_position_weight
    return base_max_position_weight * 0.5


def _row_text(row: pd.Series, *column_names: str) -> str:
    """Extract first non-null text value from row for given column names."""
    for col in column_names:
        if col in row and pd.notna(row[col]):
            val = str(row[col]).strip()
            if val:
                return val
    return ""


def _row_float(row: pd.Series, *column_names: str) -> float | None:
    """Extract first non-null float value from row for given column names."""
    for col in column_names:
        if col in row and pd.notna(row[col]):
            try:
                return float(row[col])
            except (TypeError, ValueError):
                continue
    return None


def _broker_row_matches_order(order_row: pd.Series, pending_order) -> bool:
    """Check if a broker order row matches a pending order."""
    pending_order_id = (
        str(pending_order.order_id).strip()
        if pending_order.order_id is not None
        else None
    )
    broker_order_id = _row_text(order_row, "order_id", "orderid", "id")
    if pending_order_id and broker_order_id:
        return pending_order_id == broker_order_id

    pending_symbol = str(pending_order.symbol).strip() if pending_order.symbol else None
    broker_symbol = _row_text(order_row, "symbol", "code", "ticker")
    if pending_symbol != broker_symbol:
        return False

    pending_side = str(pending_order.side).upper() if pending_order.side else None
    broker_side = _row_text(order_row, "side", "order_side", "direction").upper()
    if pending_side != broker_side:
        return False

    pending_qty = float(pending_order.quantity) if pending_order.quantity else None
    broker_qty = _row_float(order_row, "quantity", "qty", "order_qty")
    if pending_qty is not None and broker_qty is not None:
        return abs(pending_qty - broker_qty) < 1e-6

    return True


def _reconcile_pending_orders(state_store: StateStore, trade_client) -> int:
    get_pending_orders = getattr(state_store, "get_pending_orders", None)
    update_order_status = getattr(state_store, "update_order_status", None)
    get_order_frame = getattr(trade_client, "get_order_frame", None)
    if not callable(get_pending_orders) or not callable(update_order_status):
        return 0
    if not callable(get_order_frame):
        return 0

    pending_orders = get_pending_orders()
    if not pending_orders:
        return 0

    try:
        order_frame = get_order_frame(refresh_cache=True)
    except Exception as exc:
        logger.warning(
            "Skipping pending order reconcile after order fetch failed: %s", exc
        )
        return 0

    # Validate order_frame is a pandas DataFrame before accessing attributes
    if not isinstance(order_frame, pd.DataFrame):
        return 0
    if order_frame.empty:
        return 0

    reconciled = 0
    try:
        for pending_order in pending_orders:
            for _, order_row in order_frame.iterrows():
                if not _broker_row_matches_order(order_row, pending_order):
                    continue

                from moomoo_bot.state import _normalize_order_status

                broker_status = _normalize_order_status(
                    order_row.get("order_status") or order_row.get("status")
                )
                if not broker_status:
                    break

                order_id = _row_text(order_row, "order_id", "orderid", "id")
                if not order_id and pending_order.order_id is not None:
                    order_id = str(pending_order.order_id).strip()
                if not order_id:
                    break

                filled_quantity = _row_float(
                    order_row,
                    "filled_quantity",
                    "filled_qty",
                    "dealt_qty",
                    "deal_qty",
                    "qty",
                )
                if filled_quantity is None:
                    filled_quantity = float(pending_order.filled_quantity or 0.0)

                fill_price = _row_float(
                    order_row,
                    "avg_fill_price",
                    "avg_price",
                    "dealt_avg_price",
                    "deal_avg_price",
                    "fill_price",
                    "dealt_price",
                    "price",
                )
                broker_accepted_price = _row_float(
                    order_row,
                    "price",
                    "order_price",
                    "submitted_price",
                )
                fee_amount = _row_float(
                    order_row,
                    "fee_amount",
                    "total_fee",
                    "fee",
                    "commission",
                    "transaction_fee",
                )
                filled_at = _row_text(
                    order_row,
                    "updated_time",
                    "updated_at",
                    "create_time",
                    "created_at",
                    "fill_time",
                )

                update_order_status(
                    order_id,
                    broker_status,
                    filled_quantity,
                    fill_price=fill_price,
                    broker_accepted_price=broker_accepted_price,
                    fee_amount=fee_amount,
                    filled_at=filled_at or None,
                )
                reconciled += 1
                break
    except TypeError as exc:
        logger.warning(
            "Skipping pending order reconcile due to unexpected row type: %s", exc
        )
        return 0

    return reconciled


def _cleanup_equity_history(state_store: StateStore, keep_days: int) -> int:
    cleanup_old_equity = getattr(state_store, "cleanup_old_equity", None)
    if not callable(cleanup_old_equity):
        return 0
    return cleanup_old_equity(keep_days)


def _execute_trading_cycle(
    *,
    settings,
    trade_env: TrdEnv,
    symbols: list[str],
    benchmark_symbol: str,
    history_days: int,
    capital: float | None,
    fx_jpy_per_usd: float | None,
    minimum_order_value: float,
    max_position_weight: float | None,
    quote_client: MoomooOpenDClient | None,
    trade_client: MoomooPaperTradeClient | None,
    strategy: Strategy | None,
    state_store: StateStore | None,
    submit_orders: bool,
    auto_mode: bool,
    mode_label: str,
    poll_seconds: int | None = None,
    max_consecutive_failures: int | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> bool:
    """Execute a single trading cycle. Returns True if successful, False if halted by kill switch."""
    selected_symbols = symbols
    benchmark_label = benchmark_symbol
    paper_capital = capital if capital is not None else settings.initial_capital
    resolved_fx_rate = (
        fx_jpy_per_usd if fx_jpy_per_usd is not None else settings.fx_jpy_per_usd
    )
    paper_capital_usd = convert_capital_to_usd(
        paper_capital, settings.capital_currency, resolved_fx_rate
    )

    if _is_kill_switch_active():
        message = _kill_switch_message()
        logger.warning(message)
        console.print(message)
        return False

    owns_quote_client = quote_client is None
    owns_trade_client = trade_client is None
    owns_state_store = state_store is None
    if owns_quote_client:
        quote_client = MoomooOpenDClient(
            host=settings.opend_host, port=settings.opend_port
        )
    if owns_trade_client:
        try:
            trade_client = MoomooPaperTradeClient(
                host=settings.opend_host, port=settings.opend_port, trd_env=trade_env
            )
        except Exception:
            if owns_quote_client:
                quote_client.close()
            raise
    if owns_state_store:
        try:
            state_store = StateStore(
                db_path=settings.state_db_path,
                execution_mode=settings.execution_mode,
            )
        except Exception:
            if owns_trade_client and trade_client is not None:
                trade_client.close()
            if owns_quote_client and quote_client is not None:
                quote_client.close()
            raise
    strategy = strategy or _build_monthly_strategy(settings)
    include_benchmark_in_prices = _requires_benchmark_prices(strategy)

    try:
        persistent_risk_state = state_store.load_risk_state()
        risk_state = _restore_risk_state(persistent_risk_state)
        position_frame = trade_client.get_position_frame()
        current_positions = _position_quantities(position_frame)
        symbol_universe: list[str] = list(
            dict.fromkeys(
                [
                    *selected_symbols,
                    *current_positions.keys(),
                    *([benchmark_label] if include_benchmark_in_prices else []),
                ]
            )
        )
        price_frame, benchmark_series = quote_client.fetch_price_panel(
            symbol_universe,
            benchmark_label,
            history_days=history_days,
            include_benchmark_in_prices=include_benchmark_in_prices,
        )
        historical_latest_prices = {
            symbol: float(price_frame.iloc[-1][symbol])
            for symbol in price_frame.columns
        }
        latest_prices = _resolve_order_prices(
            quote_client, symbol_universe, historical_latest_prices
        )
        order_price_frame = _overlay_latest_prices(price_frame, latest_prices)
        market_state = _fetch_market_state(quote_client, benchmark_label)
        market_open = _is_regular_market_open(market_state)
        account_value = (
            trade_client.get_account_value() if capital is None else paper_capital_usd
        )
        market_date = _market_date_for_frame(price_frame)
        _prepare_persistent_state_for_market_date(persistent_risk_state, market_date)
        _record_state_snapshot(
            state_store, account_value, current_positions, latest_prices, market_date
        )
        _reconcile_pending_orders(state_store, trade_client)
        _cleanup_equity_history(state_store, settings.equity_retention_days)
        _clear_expired_daily_loss_halt(risk_state, persistent_risk_state, market_date)

        if risk_state.halted and _is_daily_loss_halt(risk_state.halted_reason):
            _save_risk_state(
                state_store,
                risk_state,
                persistent_risk_state,
                market_date,
                account_value,
            )
            liquidation_orders = _build_risk_liquidation_orders(
                current_positions,
                latest_prices,
                risk_state.halted_reason or "daily_loss_limit",
                settings,
                market_open,
            )
            if auto_mode:
                console.print(
                    f"{market_date}: trading halted for the day - {risk_state.halted_reason}"
                )
            else:
                console.print(
                    f"Trading halted for market date {market_date}: {risk_state.halted_reason}"
                )
            _render_and_submit_risk_liquidation(
                trade_client,
                liquidation_orders,
                current_positions,
                mode_label,
                submit_orders=submit_orders,
                state_store=state_store,
            )
            return True

        shock_reason = detect_market_shock(
            benchmark_series, settings.market_shock_drop_pct
        )
        if shock_reason:
            _save_risk_state(
                state_store,
                risk_state,
                persistent_risk_state,
                market_date,
                account_value,
            )
            logger.warning("Risk stop: %s", shock_reason)
            console.print(f"Risk stop: {shock_reason}")
            render_risk_orders([], current_positions, "Risk Stop Orders")
            return True

        daily_loss_reason = detect_daily_loss_limit(
            _daily_loss_reference(state_store, market_date),
            account_value,
            settings.daily_loss_limit_pct,
        )
        if daily_loss_reason:
            risk_state.halted = True
            risk_state.halted_reason = daily_loss_reason
            risk_state.drawdown_tier = max(risk_state.drawdown_tier, 2)
            _save_risk_state(
                state_store,
                risk_state,
                persistent_risk_state,
                market_date,
                account_value,
            )
            liquidation_orders = _build_risk_liquidation_orders(
                current_positions,
                latest_prices,
                daily_loss_reason,
                settings,
                market_open,
            )
            console.print(f"Daily loss limit triggered: {daily_loss_reason}")
            _render_and_submit_risk_liquidation(
                trade_client,
                liquidation_orders,
                current_positions,
                mode_label,
                submit_orders=submit_orders,
                state_store=state_store,
            )
            return True

        drawdown_reason = update_drawdown_state(
            account_value,
            risk_state,
            settings.max_drawdown_pct,
            settings.max_drawdown_reset_pct,
        )
        if drawdown_reason:
            _save_risk_state(
                state_store,
                risk_state,
                persistent_risk_state,
                market_date,
                account_value,
            )
            liquidation_orders = _build_risk_liquidation_orders(
                current_positions,
                latest_prices,
                drawdown_reason,
                settings,
                market_open,
            )
            render_paper_plan(
                PaperPlan(
                    as_of=price_frame.index[-1],
                    capital=account_value,
                    reason=drawdown_reason,
                    allocations=[],
                    cash_remaining=account_value,
                ),
                benchmark_label,
                ", ".join(str(s) for s in symbol_universe),
                benchmark_series,
            )
            if capital is None:
                console.print(
                    f"Capital input: {account_value:,.2f} USD (from {mode_label} account)"
                )
            else:
                console.print(
                    f"Capital input: {capital:,.2f} {settings.capital_currency}"
                )
                console.print(f"Capital used for sizing: {account_value:,.2f} USD")
            _render_and_submit_risk_liquidation(
                trade_client,
                liquidation_orders,
                current_positions,
                mode_label,
                submit_orders=submit_orders,
                state_store=state_store,
            )
            return True

        _save_risk_state(
            state_store,
            risk_state,
            persistent_risk_state,
            market_date,
            account_value,
        )

        decision = strategy.decide(price_frame, price_frame.index[-1])
        plan = build_paper_plan(
            order_price_frame,
            decision,
            account_value,
            minimum_order_value=minimum_order_value,
            max_position_weight=_effective_max_position_weight(
                max_position_weight or settings.max_single_position_weight, risk_state
            ),
            fractional_share_precision=settings.fractional_share_precision,
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
            fractional_share_precision=settings.fractional_share_precision,
        )
        instructions = _reprice_orders(instructions, latest_prices)
        risk_orders = _reprice_orders(risk_orders, latest_prices)

        render_paper_trade_plan(
            plan,
            benchmark_label,
            ", ".join(str(s) for s in symbol_universe),
            benchmark_series,
            current_positions,
            instructions,
        )
        if capital is None:
            console.print(
                f"Capital input: {account_value:,.2f} USD (from {mode_label} account)"
            )
        else:
            console.print(f"Capital input: {capital:,.2f} {settings.capital_currency}")
            console.print(f"Capital used for sizing: {account_value:,.2f} USD")
        if not market_open:
            console.print(
                f"Market state: {market_state}; buy orders will use ETH session."
            )

        if submit_orders:
            order_cap_reason = _daily_order_cap_reason(
                persistent_risk_state,
                market_date,
                len(risk_orders) + len(instructions),
                settings.max_daily_orders,
            )
            if order_cap_reason is not None:
                _save_risk_state(
                    state_store,
                    risk_state,
                    persistent_risk_state,
                    market_date,
                    account_value,
                )
                logger.warning(order_cap_reason)
                console.print(f"Order cap active: {order_cap_reason}")
                return True

        if risk_orders:
            render_risk_orders(risk_orders, current_positions, "Risk Exit Orders")
            if submit_orders:
                console.print(f"Submitting {mode_label} risk exit orders...")
                submitted_risk_orders = _submit_orders_with_duplicate_guard(
                    trade_client,
                    risk_orders,
                    mode_label,
                    render_order_response,
                    state_store=state_store,
                )
                _record_submitted_order_count(
                    state_store,
                    risk_state,
                    persistent_risk_state,
                    market_date,
                    account_value,
                    submitted_risk_orders or 0,
                )

        if instructions:
            if submit_orders:
                console.print(f"Submitting {mode_label} orders...")
                submitted_orders = _submit_orders_with_duplicate_guard(
                    trade_client,
                    instructions,
                    mode_label,
                    render_order_response,
                    state_store=state_store,
                )
                _record_submitted_order_count(
                    state_store,
                    risk_state,
                    persistent_risk_state,
                    market_date,
                    account_value,
                    submitted_orders or 0,
                )
            else:
                console.print("paper-run preview only; no orders submitted.")
        elif not risk_orders:
            console.print("No paper orders were required.")

        return True

    finally:
        if owns_state_store:
            state_store.close()
        if owns_trade_client:
            trade_client.close()
        if owns_quote_client:
            quote_client.close()


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
    """Execute a single trading decision and optionally submit orders."""
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
    state_store: StateStore | None = None,
) -> None:
    """Run continuous monitoring loop for paper trading."""
    sleep_fn = sleep_fn or sleep
    mode_label = _trade_mode_label(TrdEnv.SIMULATE)
    console.print(
        f"Starting auto-run monitor for {', '.join(symbols)} vs {benchmark_symbol}; "
        f"polling every {poll_seconds} seconds."
    )

    if _is_kill_switch_active():
        message = _kill_switch_message()
        logger.warning(message)
        console.print(message)
        return

    consecutive_failures = 0
    while True:
        try:
            if _is_kill_switch_active():
                message = _kill_switch_message()
                logger.warning(message)
                console.print(message)
                break

            result = _execute_trading_cycle(
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
            if consecutive_failures >= max_consecutive_failures:
                logger.error("auto-run stopped after repeated failures.")
                console.print("auto-run stopped after repeated failures.")
                break
            sleep_fn(wait_seconds)
