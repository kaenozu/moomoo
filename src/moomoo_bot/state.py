"""State persistence module.

Purpose: Persist trading state (risk, positions, orders, equity) to SQLite.
         Dataclass definitions live in state_types.py for size management.
         Schema management lives in state_schema.py.
         Query methods live in state_queries.py.
         Fill/tax-lot ledger methods live in state_ledger.py.
Related: state_types.py, state_schema.py, state_queries.py, state_ledger.py, orchestrator/, risk.py.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path

from moomoo_bot.state_types import (  # noqa: F401 — re-exported for backward compat
    EquitySnapshot,
    ExecutionAuditSummary,
    ExecutionFillRecord,
    OrderRecord,
    PersistentRiskState,
    TaxLotRealizationRecord,
    TaxLotRecord,
    _equity_snapshot_from_row,
    _execution_fill_record_from_row,
    _order_record_from_row,
    _tax_lot_realization_from_row,
    _tax_lot_record_from_row,
)
from moomoo_bot.state_schema import (  # noqa: F401 — re-exported for backward compat
    _PENDING_ORDER_STATUSES,
    _ensure_tables,
    _is_final_order_status,
    _normalize_order_status,
)
from moomoo_bot.state_queries import _QueryMixin  # noqa: F401 — re-exported via class
from moomoo_bot.state_ledger import (  # noqa: F401 — re-exported for backward compat
    _LedgerMixin,
    _row_float_value,
    _derive_incremental_fill_price,
    _calculate_slippage_amount,
    _proportional_share,
)
from moomoo_bot.row_utils import utc_now_iso as _utc_now_iso

logger = logging.getLogger(__name__)

_DEFAULT_DB_DIR = Path.home() / ".moomoo_bot"
_DEFAULT_DB_NAME = "state.db"
_DEFAULT_DB_NAMES_BY_MODE = {
    "paper": "paper-state.db",
    "live": "live-state.db",
}


def resolve_state_db_path(
    db_path: Path | str | None = None,
    execution_mode: str | None = None,
) -> Path:
    if db_path is not None:
        return Path(db_path).expanduser()

    normalized_mode = str(execution_mode or "").strip().lower()
    default_name = _DEFAULT_DB_NAMES_BY_MODE.get(normalized_mode, _DEFAULT_DB_NAME)
    return (_DEFAULT_DB_DIR / default_name).expanduser()


class StateStore(_QueryMixin, _LedgerMixin):
    """SQLite-backed state persistence for the trading bot."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        execution_mode: str | None = None,
    ) -> None:
        self.db_path = resolve_state_db_path(
            db_path=db_path,
            execution_mode=execution_mode,
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._ensure_tables()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensure connection is closed."""
        self.close()

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(
                    str(self.db_path), timeout=10, check_same_thread=False
                )
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA wal_autocheckpoint=1000")
                self._conn.execute("PRAGMA foreign_keys=ON")
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _ensure_tables(self) -> None:
        conn = self._connect()
        _ensure_tables(conn)

    def load_risk_state(self) -> PersistentRiskState:
        conn = self._connect()
        with self._lock:
            row = conn.execute("SELECT * FROM risk_state WHERE id = 1").fetchone()
        if row is None:
            return PersistentRiskState()
        return PersistentRiskState(
            peak_account_value=row["peak_account_value"],
            halted=bool(row["halted"]),
            halted_reason=row["halted_reason"],
            drawdown_tier=row["drawdown_tier"] or 0,
            daily_order_count=row["daily_order_count"] or 0,
            daily_order_date=row["daily_order_date"],
            last_equity_value=row["last_equity_value"],
            updated_at=row["updated_at"],
            rule_violation_count=row["rule_violation_count"] or 0,
        )

    def save_risk_state(self, state: PersistentRiskState) -> None:
        now = _utc_now_iso()
        conn = self._connect()
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    UPDATE risk_state SET
                        peak_account_value = ?,
                        halted = ?,
                        halted_reason = ?,
                        drawdown_tier = ?,
                        daily_order_count = ?,
                        daily_order_date = ?,
                        last_equity_value = ?,
                        rule_violation_count = ?,
                        updated_at = ?
                    WHERE id = 1
                """,
                    (
                        state.peak_account_value,
                        int(state.halted),
                        state.halted_reason,
                        state.drawdown_tier,
                        state.daily_order_count,
                        state.daily_order_date,
                        state.last_equity_value,
                        state.rule_violation_count,
                        now,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # --- Orders ---

    def record_order(self, record: OrderRecord) -> int:
        conn = self._connect()
        normalized_status = _normalize_order_status(record.status) or "submitted"
        filled_at = record.filled_at
        if filled_at is None and _is_final_order_status(normalized_status):
            filled_at = _utc_now_iso()

        # order_id must be non-empty string; empty/None become rejecting
        raw_order_id = record.order_id
        if raw_order_id is None:
            raise ValueError("order_id is required when recording an order")
        order_id_text = str(raw_order_id).strip()
        if not order_id_text:
            raise ValueError("order_id cannot be empty")

        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    """
                    INSERT INTO order_history
                        (
                            order_id,
                            symbol,
                            side,
                            quantity,
                            price,
                            status,
                            reason,
                            filled_quantity,
                            submitted_at,
                            filled_at,
                            broker_accepted_price,
                            avg_fill_price,
                            cumulative_fee_amount,
                            cumulative_slippage_amount
                        )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        order_id_text,
                        record.symbol,
                        record.side,
                        record.quantity,
                        record.price,
                        normalized_status,
                        record.reason,
                        record.filled_quantity,
                        record.submitted_at or _utc_now_iso(),
                        filled_at,
                        record.broker_accepted_price,
                        record.avg_fill_price,
                        record.cumulative_fee_amount,
                        record.cumulative_slippage_amount,
                    ),
                )
                conn.commit()
                return cursor.lastrowid
            except Exception:
                conn.rollback()
                raise

    def update_order_status(
        self,
        order_id: str,
        status: str,
        filled_quantity: float,
        fill_price: float | None = None,
        broker_accepted_price: float | None = None,
        fee_amount: float | None = None,
        filled_at: str | None = None,
    ) -> None:
        conn = self._connect()
        normalized_status = _normalize_order_status(status)
        if not normalized_status:
            return

        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                order_row = conn.execute(
                    "SELECT * FROM order_history WHERE order_id = ? ORDER BY id DESC LIMIT 1",
                    (str(order_id),),
                ).fetchone()
                if order_row is None:
                    conn.rollback()
                    logger.warning(
                        "Order %s not found in database, skipping status update",
                        order_id,
                    )
                    return

                previous_filled_quantity = float(order_row["filled_quantity"] or 0.0)
                new_filled_quantity = max(
                    float(filled_quantity), previous_filled_quantity
                )
                fill_delta = (
                    (new_filled_quantity - previous_filled_quantity)
                    if (new_filled_quantity - previous_filled_quantity) > 1e-6
                    else 0.0
                )

                previous_fee_total = float(order_row["cumulative_fee_amount"] or 0.0)
                new_fee_total = previous_fee_total
                if fee_amount is not None:
                    new_fee_total = max(float(fee_amount), previous_fee_total)
                fee_delta = max(0.0, new_fee_total - previous_fee_total)

                previous_avg_fill_price = _row_float_value(order_row["avg_fill_price"])
                new_avg_fill_price = (
                    float(fill_price)
                    if fill_price is not None
                    else previous_avg_fill_price
                )
                derived_fill_price = _derive_incremental_fill_price(
                    previous_filled_quantity,
                    previous_avg_fill_price,
                    new_filled_quantity,
                    new_avg_fill_price,
                    float(order_row["price"]),
                )
                effective_filled_at = filled_at or _utc_now_iso()
                effective_broker_price = (
                    float(broker_accepted_price)
                    if broker_accepted_price is not None
                    else _row_float_value(order_row["broker_accepted_price"])
                )
                if effective_broker_price is None:
                    effective_broker_price = float(order_row["price"])

                previous_cumulative_slippage = float(
                    order_row["cumulative_slippage_amount"] or 0.0
                )
                slippage_delta = 0.0
                if fill_delta > 0.0:
                    execution_fill = ExecutionFillRecord(
                        order_id=str(order_id),
                        symbol=str(order_row["symbol"]),
                        side=str(order_row["side"]),
                        fill_quantity=fill_delta,
                        intended_price=float(order_row["price"]),
                        broker_accepted_price=effective_broker_price,
                        fill_price=derived_fill_price,
                        fee_amount=fee_delta,
                        slippage_amount=_calculate_slippage_amount(
                            str(order_row["side"]),
                            fill_delta,
                            float(order_row["price"]),
                            derived_fill_price,
                        ),
                        filled_at=effective_filled_at,
                    )
                    slippage_delta = execution_fill.slippage_amount
                    self._record_execution_fill(conn, execution_fill)
                    self._apply_execution_fill_to_tax_lots(conn, execution_fill)

                final_filled_at = (
                    effective_filled_at
                    if _is_final_order_status(normalized_status)
                    else None
                )
                conn.execute(
                    """
                    UPDATE order_history SET
                        status = ?,
                        filled_quantity = ?,
                        filled_at = COALESCE(?, filled_at),
                        broker_accepted_price = COALESCE(?, broker_accepted_price),
                        avg_fill_price = COALESCE(?, avg_fill_price),
                        cumulative_fee_amount = ?,
                        cumulative_slippage_amount = ?
                    WHERE order_id = ?
                """,
                    (
                        normalized_status,
                        new_filled_quantity,
                        final_filled_at,
                        effective_broker_price,
                        new_avg_fill_price,
                        new_fee_total,
                        previous_cumulative_slippage + slippage_delta,
                        str(order_id),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def update_order_id(self, old_order_id: str, new_order_id: str) -> None:
        """Replace a temporary internal order_id with the actual broker order_id."""
        if not old_order_id or not new_order_id:
            raise ValueError(
                "old_order_id and new_order_id must both be non-empty strings"
            )
        conn = self._connect()
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE order_history SET order_id = ? WHERE order_id = ?",
                    (str(new_order_id), str(old_order_id)),
                )
                conn.execute(
                    "UPDATE execution_fill_ledger SET order_id = ? WHERE order_id = ?",
                    (str(new_order_id), str(old_order_id)),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # --- Equity Curve ---

    def record_equity(
        self,
        account_value: float,
        cash: float,
        positions: dict[str, float],
        market_date: str | None = None,
    ) -> None:
        conn = self._connect()
        now = _utc_now_iso()
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO equity_curve (timestamp, account_value, cash, positions_json, market_date)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (
                        now,
                        account_value,
                        cash,
                        json.dumps(positions),
                        market_date,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # --- Position Log ---

    def record_positions(
        self, positions: dict[str, float], prices: dict[str, float]
    ) -> None:
        conn = self._connect()
        now = _utc_now_iso()
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                for symbol, qty in positions.items():
                    price = prices.get(symbol, 0.0)
                    conn.execute(
                        """
                        INSERT INTO position_log (timestamp, symbol, quantity, price, value)
                        VALUES (?, ?, ?, ?, ?)
                    """,
                        (now, symbol, qty, price, qty * price),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # --- Housekeeping ---

    def cleanup_old_equity(self, keep_days: int = 365) -> int:
        conn = self._connect()
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    "DELETE FROM equity_curve WHERE timestamp < datetime('now', ?)",
                    (f"-{keep_days} days",),
                )
                conn.commit()
                return cursor.rowcount
            except Exception:
                conn.rollback()
                raise
