"""Trading cycle execution module.

Purpose: Core _execute_trading_cycle function and order reconciliation.
         Uses _orch.xxx for monkeypatched names to preserve test compatibility.
Related: orchestrator/__init__.py, orchestrator/helpers.py.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pandas as pd

from moomoo import TrdEnv

from moomoo_bot.cli_helpers import (
    build_monthly_strategy as _build_monthly_strategy,
    fetch_market_state as _fetch_market_state,
    is_regular_market_open as _is_regular_market_open,
    position_quantities as _position_quantities,
    requires_benchmark_prices as _requires_benchmark_prices,
)
from moomoo_bot.cli_render import (
    console,
    render_order_response,
    render_paper_plan,
    render_paper_trade_plan,
    render_risk_orders,
)
from moomoo_bot.money import convert_capital_to_usd
from moomoo_bot.notify import (
    notify_daily_limit as _notify_daily_limit,
    notify_risk_stop as _notify_risk_stop,
)
from moomoo_bot.paper import (
    PaperPlan,
    build_paper_rebalance_orders,
)
from moomoo_bot.risk import (
    build_stop_loss_take_profit_orders,
    detect_daily_loss_limit,
    detect_low_ev_condition,
    detect_market_shock,
    detect_monthly_loss_limit,
    update_drawdown_state,
)
from moomoo_bot.state import _normalize_order_status
from moomoo_bot.strategy.base import Strategy

from moomoo_bot.orchestrator.helpers import (
    build_risk_liquidation_orders,
    cleanup_equity_history,
    clear_expired_daily_loss_halt,
    daily_loss_reference,
    daily_order_cap_reason,
    effective_max_position_weight,
    is_daily_loss_halt,
    kill_switch_message,
    market_date_for_frame,
    overlay_latest_prices,
    prepare_persistent_state_for_market_date,
    record_state_snapshot,
    record_submitted_order_count,
    reprice_orders,
    restore_risk_state,
    save_risk_state,
    webhook_str,
    resolve_order_prices,
)

import moomoo_bot.orchestrator as _orch_module

logger = logging.getLogger(__name__)


def broker_row_matches_order(order_row: pd.Series, pending_order) -> bool:
    from moomoo_bot.row_utils import row_text, row_float

    pending_order_id = (
        str(pending_order.order_id).strip()
        if pending_order.order_id is not None
        else None
    )
    broker_order_id = row_text(order_row, "order_id", "orderid", "id")
    if pending_order_id and broker_order_id:
        return pending_order_id == broker_order_id

    pending_symbol = str(pending_order.symbol).strip() if pending_order.symbol else None
    broker_symbol = row_text(order_row, "symbol", "code", "ticker")
    if pending_symbol != broker_symbol:
        return False

    pending_side = str(pending_order.side).upper() if pending_order.side else None
    broker_side = row_text(order_row, "side", "order_side", "direction").upper()
    if pending_side != broker_side:
        return False

    pending_qty = float(pending_order.quantity) if pending_order.quantity else None
    broker_qty = row_float(order_row, "quantity", "qty", "order_qty")
    if pending_qty is not None and broker_qty is not None:
        return abs(pending_qty - broker_qty) < 1e-6

    return False


def reconcile_pending_orders(state_store, trade_client) -> int:
    from moomoo_bot.row_utils import row_text, row_float

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

    if not isinstance(order_frame, pd.DataFrame):
        return 0
    if order_frame.empty:
        return 0

    reconciled = 0
    for pending_order in pending_orders:
        try:
            for _, order_row in order_frame.iterrows():
                if not _orch_module._broker_row_matches_order(order_row, pending_order):
                    continue

                broker_status = _normalize_order_status(
                    order_row.get("order_status") or order_row.get("status")
                )
                if not broker_status:
                    break

                order_id = row_text(order_row, "order_id", "orderid", "id")
                if not order_id and pending_order.order_id is not None:
                    order_id = str(pending_order.order_id).strip()
                if not order_id:
                    continue

                filled_quantity = row_float(
                    order_row,
                    "filled_quantity",
                    "filled_qty",
                    "dealt_qty",
                    "deal_qty",
                    "qty",
                )
                if filled_quantity is None:
                    filled_quantity = float(pending_order.filled_quantity or 0.0)

                fill_price = row_float(
                    order_row,
                    "avg_fill_price",
                    "avg_price",
                    "dealt_avg_price",
                    "deal_avg_price",
                    "fill_price",
                    "dealt_price",
                    "price",
                )
                broker_accepted_price = row_float(
                    order_row,
                    "price",
                    "order_price",
                    "submitted_price",
                )
                fee_amount = row_float(
                    order_row,
                    "fee_amount",
                    "total_fee",
                    "fee",
                    "commission",
                    "transaction_fee",
                )
                filled_at = row_text(
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
        except (TypeError, KeyError, AttributeError) as exc:
            logger.warning(
                "Skipping pending order reconcile due to unexpected row type: %s", exc
            )
            continue

    return reconciled


def render_and_submit_risk_liquidation(
    trade_client,
    liquidation_orders,
    current_positions: dict[str, float],
    mode_label: str,
    submit_orders: bool = True,
    state_store=None,
) -> None:
    render_risk_orders(liquidation_orders, current_positions, "Risk Stop Orders")
    if not submit_orders or not liquidation_orders:
        return
    console.print(f"Submitting {mode_label} risk stop liquidation orders...")
    _orch_module._submit_orders_with_duplicate_guard(
        trade_client,
        liquidation_orders,
        mode_label,
        render_order_response,
        state_store=state_store,
    )


def _run_risk_checks(
    *,
    risk_state,
    persistent_risk_state,
    state_store,
    settings,
    account_value: float,
    market_date: str,
    current_positions: dict[str, float],
    latest_prices: dict[str, float],
    benchmark_series,
    market_open: bool,
    mode_label: str,
    auto_mode: bool,
    submit_orders: bool,
    trade_client,
    price_frame,
    symbol_universe: list[str],
    benchmark_label: str,
    capital: float | None,
) -> tuple[bool, bool]:
    if risk_state.halted and is_daily_loss_halt(risk_state.halted_reason):
        save_risk_state(
            state_store,
            risk_state,
            persistent_risk_state,
            market_date,
            account_value,
        )
        liquidation_orders = build_risk_liquidation_orders(
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
        render_and_submit_risk_liquidation(
            trade_client,
            liquidation_orders,
            current_positions,
            mode_label,
            submit_orders=submit_orders,
            state_store=state_store,
        )
        return True, False

    shock_reason = detect_market_shock(
        benchmark_series,
        settings.market_shock_drop_pct,
    )
    if shock_reason:
        save_risk_state(
            state_store,
            risk_state,
            persistent_risk_state,
            market_date,
            account_value,
        )
        logger.warning("Risk stop: %s", shock_reason)
        console.print(f"Risk stop: {shock_reason}")
        render_risk_orders([], current_positions, "Risk Stop Orders")
        peak = risk_state.peak_account_value or account_value
        _notify_risk_stop(
            webhook_str(settings),
            reason=shock_reason,
            account_value=account_value,
            peak_value=peak,
            drawdown_pct=(peak - account_value) / peak if peak > 0 else 0.0,
        )
        return True, False

    dl_reason = detect_daily_loss_limit(
        daily_loss_reference(state_store, market_date),
        account_value,
        settings.daily_loss_limit_pct,
    )
    if dl_reason:
        risk_state.halted = True
        risk_state.halted_reason = dl_reason
        risk_state.drawdown_tier = max(risk_state.drawdown_tier, 2)
        save_risk_state(
            state_store,
            risk_state,
            persistent_risk_state,
            market_date,
            account_value,
        )
        liquidation_orders = build_risk_liquidation_orders(
            current_positions,
            latest_prices,
            dl_reason,
            settings,
            market_open,
        )
        console.print(f"Daily loss limit triggered: {dl_reason}")
        daily_ref = daily_loss_reference(state_store, market_date) or account_value
        _notify_daily_limit(
            webhook_str(settings),
            loss_pct=(daily_ref - account_value) / daily_ref if daily_ref > 0 else 0.0,
            account_value=account_value,
        )
        render_and_submit_risk_liquidation(
            trade_client,
            liquidation_orders,
            current_positions,
            mode_label,
            submit_orders=submit_orders,
            state_store=state_store,
        )
        return True, False

    month_start_snapshot = state_store.get_equity_at_month_start(market_date)
    month_start_equity = (
        float(month_start_snapshot.account_value)
        if month_start_snapshot is not None
        else None
    )
    monthly_loss_reason = detect_monthly_loss_limit(
        month_start_equity,
        account_value,
        settings.monthly_loss_limit_pct,
    )
    if monthly_loss_reason:
        risk_state.halted = True
        risk_state.halted_reason = monthly_loss_reason
        risk_state.drawdown_tier = max(risk_state.drawdown_tier, 2)
        save_risk_state(
            state_store,
            risk_state,
            persistent_risk_state,
            market_date,
            account_value,
        )
        liquidation_orders = build_risk_liquidation_orders(
            current_positions,
            latest_prices,
            monthly_loss_reason,
            settings,
            market_open,
        )
        console.print(f"Monthly loss limit triggered: {monthly_loss_reason}")
        peak = risk_state.peak_account_value or account_value
        _notify_risk_stop(
            webhook_str(settings),
            reason=monthly_loss_reason,
            account_value=account_value,
            peak_value=peak,
            drawdown_pct=(
                (month_start_equity - account_value) / month_start_equity
                if month_start_equity and month_start_equity > 0
                else 0.0
            ),
        )
        render_and_submit_risk_liquidation(
            trade_client,
            liquidation_orders,
            current_positions,
            mode_label,
            submit_orders=submit_orders,
            state_store=state_store,
        )
        return True, False

    drawdown_reason = update_drawdown_state(
        account_value,
        risk_state,
        settings.max_drawdown_pct,
        settings.max_drawdown_reset_pct,
    )
    if drawdown_reason:
        save_risk_state(
            state_store,
            risk_state,
            persistent_risk_state,
            market_date,
            account_value,
        )
        liquidation_orders = build_risk_liquidation_orders(
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
            console.print(f"Capital input: {capital:,.2f} {settings.capital_currency}")
            console.print(f"Capital used for sizing: {account_value:,.2f} USD")
        peak = risk_state.peak_account_value or account_value
        _notify_risk_stop(
            webhook_str(settings),
            reason=drawdown_reason,
            account_value=account_value,
            peak_value=peak,
            drawdown_pct=(peak - account_value) / peak if peak > 0 else 0.0,
        )
        render_and_submit_risk_liquidation(
            trade_client,
            liquidation_orders,
            current_positions,
            mode_label,
            submit_orders=submit_orders,
            state_store=state_store,
        )
        return True, False

    save_risk_state(
        state_store,
        risk_state,
        persistent_risk_state,
        market_date,
        account_value,
    )

    ev_should_reduce = False
    if settings.ev_lookback_trades > 0:
        recent_realizations = state_store.get_recent_realizations(
            settings.ev_lookback_trades,
        )
        ev_halt_reason, ev_should_reduce = detect_low_ev_condition(
            recent_realizations,
            settings.ev_lookback_trades,
            settings.ev_halt_threshold,
            settings.ev_reduce_threshold,
        )
        if ev_halt_reason:
            risk_state.halted = True
            risk_state.halted_reason = ev_halt_reason
            risk_state.drawdown_tier = max(risk_state.drawdown_tier, 2)
            save_risk_state(
                state_store,
                risk_state,
                persistent_risk_state,
                market_date,
                account_value,
            )
            liquidation_orders = build_risk_liquidation_orders(
                current_positions,
                latest_prices,
                ev_halt_reason,
                settings,
                market_open,
            )
            console.print(f"EV halt triggered: {ev_halt_reason}")
            peak = risk_state.peak_account_value or account_value
            _notify_risk_stop(
                webhook_str(settings),
                reason=ev_halt_reason,
                account_value=account_value,
                peak_value=peak,
                drawdown_pct=(peak - account_value) / peak if peak > 0 else 0.0,
            )
            render_and_submit_risk_liquidation(
                trade_client,
                liquidation_orders,
                current_positions,
                mode_label,
                submit_orders=submit_orders,
                state_store=state_store,
            )
            return True, False
        if ev_should_reduce:
            logger.info("EV reduce condition active: halving effective position weight")
            console.print("EV reduce active: position weight halved for this cycle")

    return False, ev_should_reduce


def execute_trading_cycle(
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
    quote_client=None,
    trade_client=None,
    strategy: Strategy | None = None,
    state_store=None,
    submit_orders: bool,
    auto_mode: bool,
    mode_label: str,
    poll_seconds: int | None = None,
    max_consecutive_failures: int | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> bool:
    MoomooOpenDClient = _orch_module.MoomooOpenDClient
    MoomooPaperTradeClient = _orch_module.MoomooPaperTradeClient
    StateStore = _orch_module.StateStore

    selected_symbols = symbols
    benchmark_label = benchmark_symbol
    paper_capital = capital if capital is not None else settings.initial_capital
    resolved_fx_rate = (
        fx_jpy_per_usd if fx_jpy_per_usd is not None else settings.fx_jpy_per_usd
    )
    requested_paper_capital_usd = convert_capital_to_usd(
        paper_capital, settings.capital_currency, resolved_fx_rate
    )

    if _orch_module._is_kill_switch_active():
        message = kill_switch_message()
        logger.warning(message)
        console.print(message)
        from moomoo_bot.notify import notify_kill_switch

        notify_kill_switch(webhook_str(settings))
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

    paper_capital_usd = requested_paper_capital_usd
    buying_power_usd: float | None = None
    buying_power_getter = getattr(trade_client, "get_buying_power", None)
    if callable(buying_power_getter):
        try:
            buying_power_usd = float(buying_power_getter())
        except Exception as exc:
            logger.warning("Failed to resolve paper buying power: %s", exc)
            requested_paper_capital_usd *= 0.5
        else:
            if buying_power_usd <= 0.0:
                buying_power_usd = 0.0
            paper_capital_usd = min(requested_paper_capital_usd, buying_power_usd)
            if paper_capital_usd < requested_paper_capital_usd:
                console.print(
                    "Paper sizing capital capped to available buying power: "
                    f"{paper_capital_usd:,.2f} USD"
                )
    strategy = strategy or _build_monthly_strategy(settings)
    include_benchmark_in_prices = _requires_benchmark_prices(strategy)

    try:
        persistent_risk_state = state_store.load_risk_state()
        risk_state = restore_risk_state(persistent_risk_state)
        if buying_power_usd is not None and buying_power_usd <= 0.0:
            logger.warning(
                "Paper account has no positive buying power; attempting repair."
            )
            console.print(
                "Paper account has no positive buying power; attempting repair."
            )
            return _orch_module.run_paper_repair(
                settings=settings,
                benchmark_symbol=benchmark_label,
                quote_client=quote_client,
                trade_client=trade_client,
                state_store=state_store,
                clear_local_state=True,
            )
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
        latest_prices = resolve_order_prices(
            quote_client, symbol_universe, historical_latest_prices
        )
        order_price_frame = overlay_latest_prices(price_frame, latest_prices)
        market_state = _fetch_market_state(quote_client, benchmark_label)
        market_open = _is_regular_market_open(market_state)
        account_value = (
            trade_client.get_account_value() if capital is None else paper_capital_usd
        )
        market_date = market_date_for_frame(price_frame)
        prepare_persistent_state_for_market_date(persistent_risk_state, market_date)
        record_state_snapshot(
            state_store, account_value, current_positions, latest_prices, market_date
        )
        reconcile_pending_orders(state_store, trade_client)
        cleanup_equity_history(state_store, settings.equity_retention_days)
        clear_expired_daily_loss_halt(risk_state, persistent_risk_state, market_date)

        risk_halted, ev_should_reduce = _run_risk_checks(
            risk_state=risk_state,
            persistent_risk_state=persistent_risk_state,
            state_store=state_store,
            settings=settings,
            account_value=account_value,
            market_date=market_date,
            current_positions=current_positions,
            latest_prices=latest_prices,
            benchmark_series=benchmark_series,
            market_open=market_open,
            mode_label=mode_label,
            auto_mode=auto_mode,
            submit_orders=submit_orders,
            trade_client=trade_client,
            price_frame=price_frame,
            symbol_universe=symbol_universe,
            benchmark_label=benchmark_label,
            capital=capital,
        )
        if risk_halted:
            return True

        effective_max_weight = effective_max_position_weight(
            max_position_weight or settings.max_single_position_weight, risk_state
        )
        if ev_should_reduce:
            logger.info(
                "EV reduce: halving max position weight to %.2f",
                effective_max_weight * 0.5,
            )
            effective_max_weight = effective_max_weight * 0.5

        decision = strategy.decide(price_frame, price_frame.index[-1])
        plan = _orch_module.build_paper_plan(
            order_price_frame,
            decision,
            account_value,
            minimum_order_value=minimum_order_value,
            max_position_weight=effective_max_weight,
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
        instructions = reprice_orders(instructions, latest_prices)
        risk_orders = reprice_orders(risk_orders, latest_prices)

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
            cap_reason = daily_order_cap_reason(
                persistent_risk_state,
                market_date,
                len(risk_orders) + len(instructions),
                settings.max_daily_orders,
            )
            if cap_reason is not None:
                save_risk_state(
                    state_store,
                    risk_state,
                    persistent_risk_state,
                    market_date,
                    account_value,
                )
                logger.warning(cap_reason)
                console.print(f"Order cap active: {cap_reason}")
                return True

        if risk_orders:
            render_risk_orders(risk_orders, current_positions, "Risk Exit Orders")
            if submit_orders:
                console.print(f"Submitting {mode_label} risk exit orders...")
                submitted_risk_orders = (
                    _orch_module._submit_orders_with_duplicate_guard(
                        trade_client,
                        risk_orders,
                        mode_label,
                        render_order_response,
                        state_store=state_store,
                    )
                )
                record_submitted_order_count(
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
                submitted_orders = _orch_module._submit_orders_with_duplicate_guard(
                    trade_client,
                    instructions,
                    mode_label,
                    render_order_response,
                    state_store=state_store,
                )
                record_submitted_order_count(
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
