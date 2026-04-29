"""Risk check orchestration module.

Purpose: Encapsulate all per-cycle risk checks (daily loss, market shock,
         drawdown, EV halt) in one place so cycle.py stays focused on flow control.
Related: orchestrator/cycle.py, orchestrator/helpers.py.
"""

from __future__ import annotations

import logging

from moomoo_bot.cli_render import console, render_paper_plan, render_risk_orders
from moomoo_bot.notify import (
    notify_daily_limit as _notify_daily_limit,
    notify_risk_stop as _notify_risk_stop,
)
from moomoo_bot.paper import PaperPlan
from moomoo_bot.risk import (
    detect_daily_loss_limit,
    detect_low_ev_condition,
    detect_market_shock,
    detect_monthly_loss_limit,
    update_drawdown_state,
)

from moomoo_bot.orchestrator.helpers import (
    build_risk_liquidation_orders,
    daily_loss_reference,
    is_daily_loss_halt,
    save_risk_state,
    webhook_str,
)

logger = logging.getLogger(__name__)


def _halt_and_liquidate(
    *,
    reason: str,
    risk_state,
    persistent_risk_state,
    state_store,
    settings,
    account_value: float,
    market_date: str,
    current_positions: dict[str, float],
    latest_prices: dict[str, float],
    market_open: bool,
    mode_label: str,
    submit_orders: bool,
    trade_client,
    set_halted: bool = True,
) -> list:
    """Common risk-halt path: save state, build liquidation orders, submit.

    Returns the list of liquidation orders for the caller to render/notify.
    """
    if set_halted:
        risk_state.halted = True
        risk_state.halted_reason = reason
        risk_state.drawdown_tier = max(risk_state.drawdown_tier, 2)

    save_risk_state(
        state_store, risk_state, persistent_risk_state, market_date, account_value,
    )
    return build_risk_liquidation_orders(
        current_positions, latest_prices, reason, settings, market_open,
    )


def check_daily_loss_halt(
    *,
    risk_state,
    persistent_risk_state,
    state_store,
    settings,
    account_value: float,
    market_date: str,
    current_positions: dict[str, float],
    latest_prices: dict[str, float],
    market_open: bool,
    mode_label: str,
    auto_mode: bool,
    submit_orders: bool,
    trade_client,
) -> tuple[bool, bool] | None:
    """Return (True, False) if already halted for daily loss; None to continue."""
    if not (risk_state.halted and is_daily_loss_halt(risk_state.halted_reason)):
        return None

    orders = _halt_and_liquidate(
        reason=risk_state.halted_reason or "daily_loss_limit",
        set_halted=False,
        risk_state=risk_state,
        persistent_risk_state=persistent_risk_state,
        state_store=state_store,
        settings=settings,
        account_value=account_value,
        market_date=market_date,
        current_positions=current_positions,
        latest_prices=latest_prices,
        market_open=market_open,
        mode_label=mode_label,
        submit_orders=submit_orders,
        trade_client=trade_client,
    )
    msg = (
        f"{market_date}: trading halted for the day - {risk_state.halted_reason}"
        if auto_mode
        else f"Trading halted for market date {market_date}: {risk_state.halted_reason}"
    )
    console.print(msg)

    from moomoo_bot.orchestrator.cycle import render_and_submit_risk_liquidation

    render_and_submit_risk_liquidation(
        trade_client, orders, current_positions, mode_label,
        submit_orders=submit_orders, state_store=state_store,
    )
    return True, False


def check_market_shock(
    *,
    benchmark_series,
    settings,
    risk_state,
    persistent_risk_state,
    state_store,
    account_value: float,
    market_date: str,
    current_positions: dict[str, float],
    latest_prices: dict[str, float],
    market_open: bool,
    mode_label: str,
    submit_orders: bool,
    trade_client,
) -> tuple[bool, bool] | None:
    """Return (True, False) if market shock detected; None to continue."""
    shock_reason = detect_market_shock(benchmark_series, settings.market_shock_drop_pct)
    if not shock_reason:
        return None

    _halt_and_liquidate(
        reason=shock_reason,
        set_halted=False,
        risk_state=risk_state,
        persistent_risk_state=persistent_risk_state,
        state_store=state_store,
        settings=settings,
        account_value=account_value,
        market_date=market_date,
        current_positions=current_positions,
        latest_prices=latest_prices,
        market_open=market_open,
        mode_label=mode_label,
        submit_orders=submit_orders,
        trade_client=trade_client,
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


def check_daily_loss_limit(
    *,
    settings,
    state_store,
    risk_state,
    persistent_risk_state,
    account_value: float,
    market_date: str,
    current_positions: dict[str, float],
    latest_prices: dict[str, float],
    market_open: bool,
    mode_label: str,
    submit_orders: bool,
    trade_client,
) -> tuple[bool, bool] | None:
    """Return (True, False) if daily loss limit breached; None to continue."""
    dl_reason = detect_daily_loss_limit(
        daily_loss_reference(state_store, market_date),
        account_value,
        settings.daily_loss_limit_pct,
    )
    if not dl_reason:
        return None

    orders = _halt_and_liquidate(
        reason=dl_reason,
        risk_state=risk_state,
        persistent_risk_state=persistent_risk_state,
        state_store=state_store,
        settings=settings,
        account_value=account_value,
        market_date=market_date,
        current_positions=current_positions,
        latest_prices=latest_prices,
        market_open=market_open,
        mode_label=mode_label,
        submit_orders=submit_orders,
        trade_client=trade_client,
    )
    console.print(f"Daily loss limit triggered: {dl_reason}")

    daily_ref = daily_loss_reference(state_store, market_date) or account_value
    _notify_daily_limit(
        webhook_str(settings),
        loss_pct=(daily_ref - account_value) / daily_ref if daily_ref > 0 else 0.0,
        account_value=account_value,
    )

    from moomoo_bot.orchestrator.cycle import render_and_submit_risk_liquidation

    render_and_submit_risk_liquidation(
        trade_client, orders, current_positions, mode_label,
        submit_orders=submit_orders, state_store=state_store,
    )
    return True, False


def check_monthly_loss_limit(
    *,
    settings,
    state_store,
    risk_state,
    persistent_risk_state,
    account_value: float,
    market_date: str,
    current_positions: dict[str, float],
    latest_prices: dict[str, float],
    market_open: bool,
    mode_label: str,
    submit_orders: bool,
    trade_client,
) -> tuple[bool, bool] | None:
    """Return (True, False) if monthly loss limit breached; None to continue."""
    month_start_snapshot = state_store.get_equity_at_month_start(market_date)
    month_start_equity = (
        float(month_start_snapshot.account_value)
        if month_start_snapshot is not None
        else None
    )
    monthly_loss_reason = detect_monthly_loss_limit(
        month_start_equity, account_value, settings.monthly_loss_limit_pct,
    )
    if not monthly_loss_reason:
        return None

    orders = _halt_and_liquidate(
        reason=monthly_loss_reason,
        risk_state=risk_state,
        persistent_risk_state=persistent_risk_state,
        state_store=state_store,
        settings=settings,
        account_value=account_value,
        market_date=market_date,
        current_positions=current_positions,
        latest_prices=latest_prices,
        market_open=market_open,
        mode_label=mode_label,
        submit_orders=submit_orders,
        trade_client=trade_client,
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

    from moomoo_bot.orchestrator.cycle import render_and_submit_risk_liquidation

    render_and_submit_risk_liquidation(
        trade_client, orders, current_positions, mode_label,
        submit_orders=submit_orders, state_store=state_store,
    )
    return True, False


def check_drawdown(
    *,
    settings,
    risk_state,
    persistent_risk_state,
    state_store,
    account_value: float,
    market_date: str,
    current_positions: dict[str, float],
    latest_prices: dict[str, float],
    market_open: bool,
    mode_label: str,
    submit_orders: bool,
    trade_client,
    price_frame,
    symbol_universe: list[str],
    benchmark_label: str,
    capital: float | None,
    benchmark_series,
) -> tuple[bool, bool] | None:
    """Return (True, False) if max drawdown breached; None to continue."""
    drawdown_reason = update_drawdown_state(
        account_value, risk_state, settings.max_drawdown_pct, settings.max_drawdown_reset_pct,
    )
    if not drawdown_reason:
        return None

    orders = _halt_and_liquidate(
        reason=drawdown_reason,
        risk_state=risk_state,
        persistent_risk_state=persistent_risk_state,
        state_store=state_store,
        settings=settings,
        account_value=account_value,
        market_date=market_date,
        current_positions=current_positions,
        latest_prices=latest_prices,
        market_open=market_open,
        mode_label=mode_label,
        submit_orders=submit_orders,
        trade_client=trade_client,
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

    from moomoo_bot.orchestrator.cycle import render_and_submit_risk_liquidation

    render_and_submit_risk_liquidation(
        trade_client, orders, current_positions, mode_label,
        submit_orders=submit_orders, state_store=state_store,
    )
    return True, False


def check_ev_halt(
    *,
    settings,
    state_store,
    risk_state,
    persistent_risk_state,
    account_value: float,
    market_date: str,
    current_positions: dict[str, float],
    latest_prices: dict[str, float],
    market_open: bool,
    mode_label: str,
    submit_orders: bool,
    trade_client,
) -> tuple[bool, bool] | None:
    """Check EV halt condition. Return (True, False) if halted; None to continue.

    Also returns (False, True) if EV reduce is active (not halted but reduce).
    """
    if settings.ev_lookback_trades <= 0:
        return None

    recent_realizations = state_store.get_recent_realizations(settings.ev_lookback_trades)
    ev_halt_reason, ev_should_reduce = detect_low_ev_condition(
        recent_realizations,
        settings.ev_lookback_trades,
        settings.ev_halt_threshold,
        settings.ev_reduce_threshold,
    )
    if not ev_halt_reason:
        if ev_should_reduce:
            logger.info("EV reduce condition active: halving effective position weight")
            console.print("EV reduce active: position weight halved for this cycle")
            return False, True
        return None

    orders = _halt_and_liquidate(
        reason=ev_halt_reason,
        risk_state=risk_state,
        persistent_risk_state=persistent_risk_state,
        state_store=state_store,
        settings=settings,
        account_value=account_value,
        market_date=market_date,
        current_positions=current_positions,
        latest_prices=latest_prices,
        market_open=market_open,
        mode_label=mode_label,
        submit_orders=submit_orders,
        trade_client=trade_client,
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

    from moomoo_bot.orchestrator.cycle import render_and_submit_risk_liquidation

    render_and_submit_risk_liquidation(
        trade_client, orders, current_positions, mode_label,
        submit_orders=submit_orders, state_store=state_store,
    )
    return True, False


def run_risk_checks(
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
    """Run all risk checks in priority order. Returns (halted, ev_should_reduce)."""
    checks_in_order = [
        lambda: check_daily_loss_halt(
            risk_state=risk_state,
            persistent_risk_state=persistent_risk_state,
            state_store=state_store,
            settings=settings,
            account_value=account_value,
            market_date=market_date,
            current_positions=current_positions,
            latest_prices=latest_prices,
            market_open=market_open,
            mode_label=mode_label,
            auto_mode=auto_mode,
            submit_orders=submit_orders,
            trade_client=trade_client,
        ),
        lambda: check_market_shock(
            benchmark_series=benchmark_series,
            settings=settings,
            risk_state=risk_state,
            persistent_risk_state=persistent_risk_state,
            state_store=state_store,
            account_value=account_value,
            market_date=market_date,
            current_positions=current_positions,
            latest_prices=latest_prices,
            market_open=market_open,
            mode_label=mode_label,
            submit_orders=submit_orders,
            trade_client=trade_client,
        ),
        lambda: check_daily_loss_limit(
            settings=settings,
            state_store=state_store,
            risk_state=risk_state,
            persistent_risk_state=persistent_risk_state,
            account_value=account_value,
            market_date=market_date,
            current_positions=current_positions,
            latest_prices=latest_prices,
            market_open=market_open,
            mode_label=mode_label,
            submit_orders=submit_orders,
            trade_client=trade_client,
        ),
        lambda: check_monthly_loss_limit(
            settings=settings,
            state_store=state_store,
            risk_state=risk_state,
            persistent_risk_state=persistent_risk_state,
            account_value=account_value,
            market_date=market_date,
            current_positions=current_positions,
            latest_prices=latest_prices,
            market_open=market_open,
            mode_label=mode_label,
            submit_orders=submit_orders,
            trade_client=trade_client,
        ),
        lambda: check_drawdown(
            settings=settings,
            risk_state=risk_state,
            persistent_risk_state=persistent_risk_state,
            state_store=state_store,
            account_value=account_value,
            market_date=market_date,
            current_positions=current_positions,
            latest_prices=latest_prices,
            market_open=market_open,
            mode_label=mode_label,
            submit_orders=submit_orders,
            trade_client=trade_client,
            price_frame=price_frame,
            symbol_universe=symbol_universe,
            benchmark_label=benchmark_label,
            capital=capital,
            benchmark_series=benchmark_series,
        ),
        lambda: check_ev_halt(
            settings=settings,
            state_store=state_store,
            risk_state=risk_state,
            persistent_risk_state=persistent_risk_state,
            account_value=account_value,
            market_date=market_date,
            current_positions=current_positions,
            latest_prices=latest_prices,
            market_open=market_open,
            mode_label=mode_label,
            submit_orders=submit_orders,
            trade_client=trade_client,
        ),
    ]

    ev_should_reduce = False
    for check_fn in checks_in_order:
        result = check_fn()
        if result is not None:
            halted, reduce = result
            if halted:
                return True, False
            if reduce:
                ev_should_reduce = True

    save_risk_state(
        state_store, risk_state, persistent_risk_state, market_date, account_value,
    )
    return False, ev_should_reduce
