"""Order submission and duplicate guard module.

Purpose: Submit trading orders with duplicate detection, rejection handling,
         and state persistence for fills.
Why: Extracted from cli_helpers.py to keep CLI rendering separate from
     order lifecycle management.
Related: cli_helpers.py, broker/paper.py, orchestrator.py, state.py.
"""

import logging
from datetime import datetime, timedelta, timezone

from moomoo import TrdSide

from moomoo_bot.exceptions import OrderRejectedError, OrderTimeoutError
from moomoo_bot.row_utils import first_non_null_frame_value, normalize_side
from moomoo_bot.state import OrderRecord

logger = logging.getLogger(__name__)

_STATE_PENDING_DUPLICATE_MAX_AGE = timedelta(minutes=10)


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
    pending_internal_id = None
    if state_store is not None:
        import uuid
        pending_internal_id = f"internal_{uuid.uuid4().hex[:8]}"
        pending_record = OrderRecord(
            order_id=pending_internal_id,
            symbol=instruction.symbol,
            side=str(instruction.side),
            quantity=float(instruction.quantity),
            price=float(instruction.price),
            status="submitting",
            reason=instruction.reason,
            filled_quantity=0.0
        )
        state_store.record_order(pending_record)

    try:
        response = trade_client.submit_order(instruction)
        render_func(instruction, response)
        if state_store is not None:
            order_record = _build_order_record(trade_client, instruction, response)
            if order_record is not None:
                if pending_internal_id is not None and getattr(state_store, "update_order_id", None):
                    state_store.update_order_id(pending_internal_id, str(order_record.order_id))
                _persist_order_and_immediate_fill(
                    state_store,
                    order_record,
                    response,
                    already_in_db=(pending_internal_id is not None),
                )
        return 1
    except Exception as exc:
        if state_store is not None and pending_internal_id is not None:
            update_order_status = getattr(state_store, "update_order_status", None)
            if callable(update_order_status):
                update_order_status(pending_internal_id, "failed", 0.0)

        if isinstance(exc, OrderTimeoutError):
            logger.warning(
                "Order timed out, checking for existing order",
                extra={"symbol": instruction.symbol, "error": str(exc)},
            )
            # 注文の照会を行い、既存の注文があればそれを反映する
            matching_order = get_matching_active_order(trade_client, instruction)
            if matching_order:
                logger.info(
                    "Found existing order matching timed out request",
                    extra={"symbol": instruction.symbol, "matching_order": matching_order},
                )
                return 1
            logger.warning("No existing order found after timeout, submission failed.", extra={"symbol": instruction.symbol})
            return 0
        elif _is_rejected_order_error(exc):
            from moomoo_bot.cli_render import console

            console.print(
                f"Skipping {mode_label} order for {instruction.symbol}: {exc}"
            )
            return 0
        elif isinstance(exc, (ConnectionError, TimeoutError, ValueError, TypeError, RuntimeError)):
            logger.warning("Order submission failed: %s", exc)
            return 0
        else:
            logger.warning("Order submission failed: %s", exc)
            raise


def _is_rejected_order_error(exc: Exception) -> bool:
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


def _persist_order_and_immediate_fill(state_store, order_record, response, already_in_db: bool = False) -> None:
    update_order_status = getattr(state_store, "update_order_status", None)
    normalized_status = str(order_record.status or "").strip().lower().replace("-", "_")
    has_immediate_fill = float(order_record.filled_quantity or 0.0) > 0.0

    if not callable(update_order_status):
        if not already_in_db:
            state_store.record_order(order_record)
        return

    if not already_in_db:
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

    if already_in_db or has_immediate_fill:
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


def _find_matching_pending_state_order(state_store, instruction):
    if state_store is None:
        return None

    getter = getattr(state_store, "get_pending_orders", None)
    if not callable(getter):
        return None

    target_symbol = str(instruction.symbol).strip().upper()
    target_side = normalize_side(instruction.side)
    target_reason = str(instruction.reason or "").strip()

    for pending_order in getter() or []:
        if str(pending_order.symbol).strip().upper() != target_symbol:
            continue
        if normalize_side(pending_order.side) != target_side:
            continue
        if str(pending_order.reason or "").strip() != target_reason:
            continue
        submitted_at = getattr(pending_order, "submitted_at", None)
        if not _is_recent_pending_order(submitted_at):
            continue
        return pending_order
    return None


def _is_recent_pending_order(submitted_at: object) -> bool:
    if not submitted_at:
        return False

    submitted_at_text = str(submitted_at).strip()
    if not submitted_at_text:
        return False

    try:
        submitted_dt = datetime.fromisoformat(submitted_at_text)
    except ValueError:
        return False

    if submitted_dt.tzinfo is None:
        submitted_dt = submitted_dt.replace(tzinfo=timezone.utc)

    return datetime.now(timezone.utc) - submitted_dt <= _STATE_PENDING_DUPLICATE_MAX_AGE
