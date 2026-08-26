"""Orchestrator helper utilities.

Purpose: Pure utility functions for the trading orchestrator.
         No monkeypatched dependencies — safe to import directly.
Related: orchestrator/__init__.py, orchestrator/cycle.py.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

from moomoo import Session

from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.kill_switch import kill_switch_path as _kill_switch_path
from moomoo_bot.risk import RiskState, build_liquidation_orders
from moomoo_bot.row_utils import row_text as _row_text, row_float as _row_float
from moomoo_bot.state import PersistentRiskState, StateStore

logger = logging.getLogger(__name__)


def webhook_str(settings) -> str:
    return str(settings.webhook_url) if settings.webhook_url else ""


def round_order_price(value: float) -> float:
    """Round a price to 2 decimal places using banker's rounding.

    Args:
        value: Price value to round

    Returns:
        Price rounded to 2 decimal places
    """
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _extract_snapshot_prices(snapshot: pd.DataFrame) -> dict[str, float]:
    extracted_prices: dict[str, float] = {}
    for _, row in snapshot.iterrows():
        code = str(row.get("code", "")).strip()
        if not code:
            continue
        try:
            last_price = float(row.get("last_price", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if last_price > 0.0:
            extracted_prices[code] = round_order_price(last_price)
    return extracted_prices


def resolve_order_prices(
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
    order_prices.update(_extract_snapshot_prices(snapshot))
    return order_prices


def overlay_latest_prices(price_frame, latest_prices: dict[str, float]):
    adjusted_frame = price_frame.copy()
    if adjusted_frame.empty:
        return adjusted_frame
    last_index = adjusted_frame.index[-1]
    for symbol, price in latest_prices.items():
        if symbol in adjusted_frame.columns:
            adjusted_frame.loc[last_index, symbol] = price
    return adjusted_frame


def reprice_orders(instructions, latest_prices: dict[str, float]):
    from dataclasses import replace

    repriced = []
    for instruction in instructions:
        price_val = latest_prices.get(instruction.symbol, instruction.price)
        if price_val is None:
            price_val = instruction.price if instruction.price is not None else 0.0
        if float(price_val) <= 0.0:
            continue
        repriced.append(
            replace(
                instruction,
                price=round_order_price(float(price_val)),
            )
        )
    return repriced


def signed_position_quantities(position_frame: pd.DataFrame) -> dict[str, float]:
    positions: dict[str, float] = {}
    for _, row in position_frame.iterrows():
        symbol = _row_text(row, "code", "symbol", "stock_code", "ticker")
        if not symbol:
            continue
        quantity = _row_float(row, "qty", "position_qty", "holding_qty", "can_use_qty")
        if quantity is None or quantity == 0.0:
            continue
        positions[symbol] = quantity
    return positions


def snapshot_latest_prices(
    quote_client: MoomooOpenDClient, symbol_universe: list[str]
) -> dict[str, float]:
    try:
        snapshot = quote_client.fetch_market_snapshot(symbol_universe)
    except Exception as exc:
        logger.warning("Snapshot fetch failed in snapshot_latest_prices: %s", exc)
        return {}
    return _extract_snapshot_prices(snapshot)


def kill_switch_message() -> str:
    return f"Kill switch active at {_kill_switch_path()}; trading halted."


def market_date_for_frame(price_frame) -> str:
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


def restore_risk_state(persistent_state: PersistentRiskState) -> RiskState:
    return RiskState(
        peak_account_value=persistent_state.peak_account_value,
        halted=persistent_state.halted,
        halted_reason=persistent_state.halted_reason,
        drawdown_tier=persistent_state.drawdown_tier,
    )


def save_risk_state(
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


def estimate_cash(
    account_value: float,
    positions: dict[str, float],
    latest_prices: dict[str, float],
) -> float:
    invested_value = sum(
        float(quantity) * float(latest_prices.get(symbol, 0.0))
        for symbol, quantity in positions.items()
    )
    return max(0.0, float(account_value - invested_value))


def record_state_snapshot(
    state_store: StateStore,
    account_value: float,
    positions: dict[str, float],
    latest_prices: dict[str, float],
    market_date: str,
) -> None:
    state_store.record_equity(
        account_value=account_value,
        cash=estimate_cash(account_value, positions, latest_prices),
        positions=positions,
        market_date=market_date,
    )
    state_store.record_positions(positions, latest_prices)


def prepare_persistent_state_for_market_date(
    persistent_state: PersistentRiskState,
    market_date: str,
) -> None:
    if persistent_state.daily_order_date == market_date:
        return
    persistent_state.daily_order_count = 0
    persistent_state.daily_order_date = market_date


def daily_order_cap_reason(
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


def record_submitted_order_count(
    state_store: StateStore,
    risk_state: RiskState,
    persistent_state: PersistentRiskState,
    market_date: str,
    account_value: float,
    submitted_order_count: int,
) -> None:
    if submitted_order_count <= 0:
        return
    prepare_persistent_state_for_market_date(persistent_state, market_date)

    conn = getattr(state_store, "_connect", None)
    write_lock = getattr(state_store, "_lock", None)
    if callable(conn) and write_lock is not None:
        db_conn = conn()
        with write_lock:
            db_conn.execute(
                "UPDATE risk_state SET daily_order_count = COALESCE(daily_order_count, 0) + ?, "
                "daily_order_date = ? WHERE id = 1",
                (submitted_order_count, market_date),
            )
            db_conn.commit()
            row = db_conn.execute(
                "SELECT daily_order_count FROM risk_state WHERE id = 1"
            ).fetchone()
            persistent_state.daily_order_count = (
                int(row["daily_order_count"]) if row else submitted_order_count
            )
        persistent_state.daily_order_date = market_date
    else:
        persistent_state.daily_order_count += submitted_order_count

    save_risk_state(
        state_store,
        risk_state,
        persistent_state,
        market_date,
        account_value,
    )


def daily_loss_reference(state_store: StateStore, market_date: str) -> float | None:
    snapshot = state_store.get_latest_equity_before_market_date(market_date)
    if snapshot is None:
        return None
    return float(snapshot.account_value)


def is_daily_loss_halt(reason: str | None) -> bool:
    return isinstance(reason, str) and reason.startswith("daily_loss_limit:")


def clear_expired_daily_loss_halt(
    risk_state: RiskState,
    persistent_state: PersistentRiskState,
    market_date: str,
) -> None:
    if not risk_state.halted or not is_daily_loss_halt(risk_state.halted_reason):
        return
    if persistent_state.daily_order_date == market_date:
        return
    risk_state.halted = False
    risk_state.halted_reason = None
    risk_state.drawdown_tier = 0


def effective_max_position_weight(
    base_max_position_weight: float, risk_state: RiskState
) -> float:
    if risk_state.halted or risk_state.drawdown_tier < 1:
        return base_max_position_weight
    return base_max_position_weight * 0.5


def build_risk_liquidation_orders(
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


def cleanup_equity_history(state_store: StateStore, keep_days: int) -> int:
    cleanup_old_equity = getattr(state_store, "cleanup_old_equity", None)
    if not callable(cleanup_old_equity):
        return 0
    return cleanup_old_equity(keep_days)
