"""State ledger module — fill recording and tax-lot mutation methods.

Purpose: Internal methods for recording execution fills and applying them to tax lots.
         Extracted from state.py as a mixin class to keep file sizes manageable.
Related: state.py, state_queries.py, state_types.py, state_schema.py.
"""

from __future__ import annotations

import logging
import sqlite3

from moomoo_bot.state_types import ExecutionFillRecord
from moomoo_bot.row_utils import (
    normalize_side as _normalize_side,
    utc_now_iso as _utc_now_iso,
)

logger = logging.getLogger(__name__)


def _row_float_value(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    return numeric_value if numeric_value == numeric_value else None


def _derive_incremental_fill_price(
    previous_quantity: float,
    previous_avg_fill_price: float | None,
    new_quantity: float,
    new_avg_fill_price: float | None,
    fallback_price: float,
) -> float:
    """Calculate the fill price for the incremental quantity in a fill update.

    Args:
        previous_quantity: Previously filled quantity
        previous_avg_fill_price: Previous average fill price
        new_quantity: New total filled quantity
        new_avg_fill_price: New average fill price
        fallback_price: Price to use if calculation cannot be determined

    Returns:
        Derived incremental fill price
    """
    if new_quantity <= previous_quantity:
        return float(new_avg_fill_price or previous_avg_fill_price or fallback_price)
    if new_avg_fill_price is None:
        return float(fallback_price)
    if previous_quantity <= 0.0 or previous_avg_fill_price is None:
        return float(new_avg_fill_price)
    incremental_quantity = new_quantity - previous_quantity
    incremental_notional = (
        float(new_avg_fill_price) * new_quantity
        - float(previous_avg_fill_price) * previous_quantity
    )
    return float(incremental_notional / incremental_quantity)


def _calculate_slippage_amount(
    side: str, quantity: float, intended_price: float, fill_price: float
) -> float:
    normalized_side = _normalize_side(side)
    if normalized_side == "SELL":
        return float((intended_price - fill_price) * quantity)
    return float((fill_price - intended_price) * quantity)


def _proportional_share(
    total_amount: float, partial_quantity: float, total_quantity: float
) -> float:
    if total_quantity <= 0.0:
        return 0.0
    return float(total_amount) * (float(partial_quantity) / float(total_quantity))


class _LedgerMixin:
    def _record_execution_fill(
        self, conn: sqlite3.Connection, fill: ExecutionFillRecord
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO execution_fill_ledger (
                order_id,
                symbol,
                side,
                fill_quantity,
                intended_price,
                broker_accepted_price,
                fill_price,
                fee_amount,
                slippage_amount,
                filled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.order_id,
                fill.symbol,
                fill.side,
                fill.fill_quantity,
                fill.intended_price,
                fill.broker_accepted_price,
                fill.fill_price,
                fill.fee_amount,
                fill.slippage_amount,
                fill.filled_at or _utc_now_iso(),
            ),
        )
        return cursor.lastrowid

    def _apply_execution_fill_to_tax_lots(
        self, conn: sqlite3.Connection, fill: ExecutionFillRecord
    ) -> None:
        normalized_side = _normalize_side(fill.side)
        if normalized_side == "BUY":
            fee_per_share = _proportional_share(
                fill.fee_amount,
                1.0,
                fill.fill_quantity,
            )
            conn.execute(
                """
                INSERT INTO tax_lots (
                    symbol,
                    opening_order_id,
                    opened_at,
                    original_quantity,
                    remaining_quantity,
                    cost_basis_price,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, 'open')
                """,
                (
                    fill.symbol,
                    fill.order_id,
                    fill.filled_at or _utc_now_iso(),
                    fill.fill_quantity,
                    fill.fill_quantity,
                    fill.fill_price + fee_per_share,
                ),
            )
            return
        if normalized_side != "SELL":
            return

        # FIFO lot consumption: ORDER BY opened_at, id ensures oldest lots are closed first.
        # This matches typical tax lot accounting and US IRS specific identification rules.
        remaining_quantity = float(fill.fill_quantity)
        open_lots = conn.execute(
            """
            SELECT * FROM tax_lots
            WHERE symbol = ? AND remaining_quantity > 0.0
            ORDER BY opened_at, id
            """,
            (fill.symbol,),
        ).fetchall()
        for lot in open_lots:
            if remaining_quantity <= 0.0:
                break

            close_quantity = min(float(lot["remaining_quantity"]), remaining_quantity)
            fee_share = _proportional_share(
                fill.fee_amount, close_quantity, fill.fill_quantity
            )
            slippage_share = _proportional_share(
                fill.slippage_amount, close_quantity, fill.fill_quantity
            )
            realized_pnl = (
                float(fill.fill_price) - float(lot["cost_basis_price"])
            ) * close_quantity - fee_share
            conn.execute(
                """
                INSERT INTO tax_lot_realizations (
                    lot_id,
                    symbol,
                    sell_order_id,
                    quantity,
                    opening_price,
                    closing_price,
                    fee_amount,
                    slippage_amount,
                    realized_pnl,
                    opened_at,
                    closed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lot["id"],
                    fill.symbol,
                    fill.order_id,
                    close_quantity,
                    lot["cost_basis_price"],
                    fill.fill_price,
                    fee_share,
                    slippage_share,
                    realized_pnl,
                    lot["opened_at"],
                    fill.filled_at or _utc_now_iso(),
                ),
            )

            updated_remaining = float(lot["remaining_quantity"]) - close_quantity
            status = "closed" if updated_remaining <= 1e-9 else "open"
            conn.execute(
                """
                UPDATE tax_lots
                SET remaining_quantity = ?, status = ?, closing_order_id = ?, closed_at = ?
                WHERE id = ?
                """,
                (
                    max(updated_remaining, 0.0),
                    status,
                    fill.order_id if status == "closed" else None,
                    (fill.filled_at or _utc_now_iso()) if status == "closed" else None,
                    lot["id"],
                ),
            )
            remaining_quantity -= close_quantity

        if remaining_quantity > 1e-9:
            fee_share = _proportional_share(
                fill.fee_amount, remaining_quantity, fill.fill_quantity
            )
            slippage_share = _proportional_share(
                fill.slippage_amount, remaining_quantity, fill.fill_quantity
            )
            conn.execute(
                """
                INSERT INTO tax_lot_realizations (
                    lot_id,
                    symbol,
                    sell_order_id,
                    quantity,
                    opening_price,
                    closing_price,
                    fee_amount,
                    slippage_amount,
                    realized_pnl,
                    opened_at,
                    closed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    None,
                    fill.symbol,
                    fill.order_id,
                    remaining_quantity,
                    None,
                    fill.fill_price,
                    fee_share,
                    slippage_share,
                    None,
                    None,
                    fill.filled_at or _utc_now_iso(),
                ),
            )
