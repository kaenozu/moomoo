"""CLI helper utilities module.

Purpose: Helper functions for CLI commands (parse, load, build strategy).
Related: cli.py.
"""

import logging
from pathlib import Path

import pandas as pd
from moomoo import TrdEnv, TrdSide

from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.row_utils import (
    first_non_null_frame_value,
    position_quantities_from_frame,
)
from moomoo_bot.strategy.momentum import (
    CoreSatelliteStrategy,
    MonthlyMomentumRotationConfig,
    MonthlyMomentumRotationStrategy,
)


def parse_symbols(raw_symbols: str | None) -> list[str]:
    if not raw_symbols:
        return []
    return [symbol.strip() for symbol in raw_symbols.split(",") if symbol.strip()]


def parse_weights(raw_weights: str | None) -> list[float]:
    if not raw_weights:
        return []
    weights: list[float] = []
    for raw_weight in raw_weights.split(","):
        raw_weight = raw_weight.strip()
        if not raw_weight:
            continue
        weight = float(raw_weight)
        if not 0.0 <= weight <= 1.0:
            raise ValueError("satellite weights must be between 0 and 1.")
        weights.append(weight)
    return weights


def fetch_market_state(client: MoomooOpenDClient, benchmark_symbol: str) -> str:
    market_state_frame = client.fetch_market_state([benchmark_symbol])
    if market_state_frame.empty:
        raise RuntimeError(f"No market state returned for {benchmark_symbol}")
    market_state = (
        str(market_state_frame.iloc[0].get("market_state", "")).strip().upper()
    )
    if not market_state:
        raise RuntimeError(f"Market state returned no value for {benchmark_symbol}")
    return market_state


def is_regular_market_open(market_state: str) -> bool:
    return market_state in {"MORNING", "AFTERNOON"}


def load_price_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    return frame.apply(pd.to_numeric, errors="coerce").dropna(how="all")


def load_benchmark_series(path: Path) -> pd.Series:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    if frame.shape[1] != 1:
        raise ValueError("benchmark_csv must contain exactly one column.")
    series = frame.iloc[:, 0].apply(pd.to_numeric, errors="coerce").dropna()
    series.name = "benchmark"
    return series


def build_monthly_strategy(
    settings,
    min_hold_days: int | None = None,
    satellite_weight: float | None = None,
    inverse_volatility: bool = False,
):
    active_strategy = MonthlyMomentumRotationStrategy(
        MonthlyMomentumRotationConfig(
            lookback_days=settings.lookback_days,
            trend_days=settings.trend_days,
            top_n=settings.top_n,
            skip_days=settings.skip_days,
            rebalance_days=settings.rebalance_days,
            min_hold_days=min_hold_days
            if min_hold_days is not None
            else settings.min_hold_days,
            inverse_volatility=inverse_volatility,
            fallback_asset_symbol=settings.fallback_asset_symbol,
            fallback_allocation=settings.fallback_allocation,
            volatility_lookback_days=settings.volatility_lookback_days,
        )
    )

    resolved_satellite_weight = (
        satellite_weight if satellite_weight is not None else settings.satellite_weight
    )
    return CoreSatelliteStrategy(
        active_strategy,
        benchmark_symbol=settings.benchmark_symbol,
        satellite_weight=resolved_satellite_weight,
    )


def requires_benchmark_prices(strategy) -> bool:
    return bool(getattr(strategy, "requires_benchmark_prices", False))


def position_quantities(position_frame: pd.DataFrame) -> dict[str, float]:
    return position_quantities_from_frame(position_frame)


def trade_mode_label(trd_env: TrdEnv) -> str:
    return "live" if trd_env == TrdEnv.REAL else "paper"


def submit_orders_with_duplicate_guard(
    trade_client,
    instructions,
    mode_label: str,
    render_func,
    state_store=None,
) -> int:
    submitted_count = 0
    for instruction in instructions:
        skip_reason = _unsupported_order_reason(instruction)
        if skip_reason is not None:
            _render_unsupported_order_skip(mode_label, instruction, skip_reason)
            continue

        duplicate_order = _find_duplicate_order(state_store, trade_client, instruction)
        if duplicate_order is not None:
            _render_duplicate_skip(
                mode_label,
                instruction,
                duplicate_order.get("order_id"),
                duplicate_order.get("order_status"),
            )
            continue

        submitted_count += _submit_single_order(
            trade_client=trade_client,
            instruction=instruction,
            render_func=render_func,
            state_store=state_store,
            mode_label=mode_label,
        )
    return submitted_count


def _find_duplicate_order(
    state_store, trade_client, instruction
) -> dict[str, object] | None:
    pending_state_order = _find_matching_pending_state_order(
        state_store,
        instruction,
    )
    if pending_state_order is not None:
        return {
            "order_id": pending_state_order.order_id,
            "order_status": pending_state_order.status,
        }

    return get_matching_active_order(trade_client, instruction)


def _render_duplicate_skip(
    mode_label: str,
    instruction,
    order_id: object,
    order_status: object,
) -> None:
    from moomoo_bot.cli_render import console

    console.print(
        f"Skipping duplicate {mode_label} order for {instruction.symbol} qty={instruction.quantity:.3f} "
        f"price={instruction.price:.2f} order_id={order_id} status={order_status}"
    )


def _render_unsupported_order_skip(mode_label: str, instruction, reason: str) -> None:
    from moomoo_bot.cli_render import console

    console.print(
        f"Skipping {mode_label} order for {instruction.symbol} qty={instruction.quantity:.3f} "
        f"price={instruction.price:.2f}: {reason}"
    )


def _unsupported_order_reason(instruction) -> str | None:
    quantity = float(getattr(instruction, "quantity", 0.0) or 0.0)
    if quantity <= 0.0:
        return "quantity must be positive"
    if getattr(instruction, "side", None) == TrdSide.BUY and quantity < 1.0:
        return "quantity below broker minimum of 1 share"
    return None


def _submit_single_order(
    *, trade_client, instruction, render_func, state_store, mode_label: str
) -> int:
    try:
        response = trade_client.submit_order(instruction)
        render_func(instruction, response)
        if state_store is not None:
            order_record = _build_order_record(trade_client, instruction, response)
            if order_record is not None:
                _persist_order_and_immediate_fill(
                    state_store,
                    order_record,
                    response,
                )
        return 1
    except (RuntimeError, ValueError, OSError, ConnectionError) as exc:
        if _is_rejected_order_error(exc):
            from moomoo_bot.cli_render import console

            console.print(
                f"Skipping {mode_label} order for {instruction.symbol}: {exc}"
            )
            return 0
        _logger = logging.getLogger(__name__)
        _logger.warning("Order submission failed: %s", exc)
        raise


def _is_rejected_order_error(exc: Exception) -> bool:
    from moomoo_bot.exceptions import OrderRejectedError

    error_msg = str(exc).lower()
    return isinstance(exc, OrderRejectedError) or any(
        keyword in error_msg
        for keyword in [
            "not enough",
            "insufficient",
            "invalid order",
            "order rejected",
            "not enough buying power",
            "exceeds position limit",
            "rejected",
        ]
    )


def _persist_order_and_immediate_fill(state_store, order_record, response) -> None:
    update_order_status = getattr(state_store, "update_order_status", None)
    normalized_status = str(order_record.status or "").strip().lower().replace("-", "_")
    has_immediate_fill = float(order_record.filled_quantity or 0.0) > 0.0

    if not callable(update_order_status) or not has_immediate_fill:
        state_store.record_order(order_record)
        return

    from moomoo_bot.state import OrderRecord

    state_store.record_order(
        OrderRecord(
            order_id=order_record.order_id,
            symbol=order_record.symbol,
            side=order_record.side,
            quantity=order_record.quantity,
            price=order_record.price,
            status="submitted",
            reason=order_record.reason,
            filled_quantity=0.0,
            submitted_at=order_record.submitted_at,
        )
    )
    update_order_status(
        str(order_record.order_id),
        normalized_status,
        float(order_record.filled_quantity or 0.0),
        fill_price=_response_first_value(
            response,
            (
                "avg_fill_price",
                "avg_price",
                "dealt_avg_price",
                "deal_avg_price",
                "fill_price",
                "dealt_price",
                "price",
            ),
        ),
        broker_accepted_price=_response_first_value(
            response,
            ("price", "order_price", "submitted_price"),
        ),
        fee_amount=_response_first_value(
            response,
            ("fee_amount", "total_fee", "fee", "commission", "transaction_fee"),
        ),
        filled_at=_response_first_value(
            response,
            ("updated_time", "updated_at", "create_time", "created_at", "fill_time"),
        ),
    )


def _build_order_record(trade_client, instruction, response):
    from moomoo_bot.state import OrderRecord

    _logger = logging.getLogger(__name__)

    order_id, status, filled_quantity = _extract_order_fields_from_response(response)

    if order_id is None:
        order_id, status, filled_quantity = _extract_order_fields_from_active_order(
            trade_client,
            instruction,
            status,
            filled_quantity,
        )

    if order_id is None:
        _logger.warning(
            "Could not determine order_id for %s %s qty=%.3f; skipping record.",
            instruction.side,
            instruction.symbol,
            instruction.quantity,
        )
        return None

    order_id_text = str(order_id).strip()
    if not order_id_text:
        return None

    return OrderRecord(
        order_id=order_id_text,
        symbol=instruction.symbol,
        side=str(instruction.side),
        quantity=float(instruction.quantity),
        price=float(instruction.price),
        status=str(status) if status is not None else "submitted",
        reason=instruction.reason,
        filled_quantity=float(filled_quantity or 0.0),
    )


def _extract_order_fields_from_response(response):
    return (
        _response_first_value(response, ("order_id", "orderid", "id")),
        _response_first_value(response, ("order_status", "status")),
        _response_first_value(
            response,
            ("filled_quantity", "filled_qty", "dealt_qty", "deal_qty", "qty"),
        ),
    )


def _extract_order_fields_from_active_order(
    trade_client,
    instruction,
    status,
    filled_quantity,
):
    matching_order = get_matching_active_order(trade_client, instruction)
    if matching_order is None:
        return None, status, filled_quantity
    return (
        matching_order.get("order_id"),
        status or matching_order.get("order_status"),
        filled_quantity
        if filled_quantity is not None
        else matching_order.get("filled_quantity"),
    )


def _response_first_value(response, candidate_fields: tuple[str, ...]):
    return first_non_null_frame_value(response, candidate_fields)


def get_matching_active_order(trade_client, instruction):
    matcher = getattr(trade_client, "get_matching_active_order", None)
    if matcher is None:
        return None
    return matcher(instruction, refresh_cache=True)


def _normalize_side_text(value: object) -> str:
    text = str(value or "").strip().upper()
    if "BUY" in text:
        return "BUY"
    if "SELL" in text:
        return "SELL"
    return text


def _find_matching_pending_state_order(state_store, instruction):
    if state_store is None:
        return None

    getter = getattr(state_store, "get_pending_orders", None)
    if not callable(getter):
        return None

    target_symbol = str(instruction.symbol).strip().upper()
    target_side = _normalize_side_text(instruction.side)
    target_reason = str(instruction.reason or "").strip()

    for pending_order in getter() or []:
        if str(pending_order.symbol).strip().upper() != target_symbol:
            continue
        if _normalize_side_text(pending_order.side) != target_side:
            continue
        if str(pending_order.reason or "").strip() != target_reason:
            continue
        return pending_order
    return None
