"""State persistence module.

Purpose: Persist trading state (risk, positions, orders, equity) to SQLite.
Related: orchestrator.py, risk.py, notify.py.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEFAULT_DB_DIR = Path.home() / ".moomoo_bot"
_DEFAULT_DB_NAME = "state.db"
_DEFAULT_DB_NAMES_BY_MODE = {
    "paper": "paper-state.db",
    "live": "live-state.db",
}

_PENDING_ORDER_STATUSES = (
    "submitted",
    "submitting",
    "waiting_submit",
    "cancelling_all",
    "cancelling_part",
    "cancelled_part",
    "filled_part",
    "partial",
)


@dataclass
class PersistentRiskState:
    peak_account_value: float | None = None
    halted: bool = False
    halted_reason: str | None = None
    drawdown_tier: int = 0  # 0=normal, 1=reduced(50%), 2=liquidated
    daily_order_count: int = 0
    daily_order_date: str | None = None
    last_equity_value: float | None = None
    updated_at: str | None = None
    rule_violation_count: int = 0


@dataclass
class OrderRecord:
    order_id: str | None = None
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    status: str = "submitted"
    reason: str = ""
    filled_quantity: float = 0.0
    submitted_at: str | None = None
    filled_at: str | None = None
    broker_accepted_price: float | None = None
    avg_fill_price: float | None = None
    cumulative_fee_amount: float = 0.0
    cumulative_slippage_amount: float = 0.0


@dataclass
class ExecutionFillRecord:
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    fill_quantity: float = 0.0
    intended_price: float = 0.0
    broker_accepted_price: float | None = None
    fill_price: float = 0.0
    fee_amount: float = 0.0
    slippage_amount: float = 0.0
    filled_at: str | None = None


@dataclass
class TaxLotRecord:
    lot_id: int | None = None
    symbol: str = ""
    opening_order_id: str | None = None
    opened_at: str | None = None
    original_quantity: float = 0.0
    remaining_quantity: float = 0.0
    cost_basis_price: float = 0.0
    status: str = "open"
    closing_order_id: str | None = None
    closed_at: str | None = None


@dataclass
class TaxLotRealizationRecord:
    realization_id: int | None = None
    lot_id: int | None = None
    symbol: str = ""
    sell_order_id: str | None = None
    quantity: float = 0.0
    opening_price: float | None = None
    closing_price: float = 0.0
    fee_amount: float = 0.0
    slippage_amount: float = 0.0
    realized_pnl: float | None = None
    opened_at: str | None = None
    closed_at: str | None = None


@dataclass
class EquitySnapshot:
    timestamp: str = ""
    account_value: float = 0.0
    cash: float = 0.0
    positions_json: str = "{}"
    market_date: str | None = None


@dataclass
class ExecutionAuditSummary:
    order_count: int = 0
    pending_order_count: int = 0
    fill_count: int = 0
    buy_fill_count: int = 0
    sell_fill_count: int = 0
    realization_count: int = 0
    open_lot_count: int = 0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    realized_pnl: float = 0.0
    last_fill_at: str | None = None
    last_realization_at: str | None = None


def _normalize_order_status(status: object) -> str:
    normalized = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"", "none", "nan"}:
        return ""
    if normalized == "filled":
        return "filled_all"
    if normalized in {"cancelled", "canceled"}:
        return "cancelled_all"
    if normalized in {"partially_filled", "partial_fill"}:
        return "filled_part"
    return normalized


def _is_final_order_status(status: str) -> bool:
    return status in {
        "filled_all",
        "cancelled_all",
        "cancelled",
        "rejected",
        "expired",
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


class StateStore:
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
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
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
                self._conn = sqlite3.connect(str(self.db_path), timeout=10)
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
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS risk_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                peak_account_value REAL,
                halted INTEGER DEFAULT 0,
                halted_reason TEXT,
                drawdown_tier INTEGER DEFAULT 0,
                daily_order_count INTEGER DEFAULT 0,
                daily_order_date TEXT,
                last_equity_value REAL,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS order_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                status TEXT DEFAULT 'submitted',
                reason TEXT DEFAULT '',
                filled_quantity REAL DEFAULT 0.0,
                submitted_at TEXT,
                filled_at TEXT,
                broker_accepted_price REAL,
                avg_fill_price REAL,
                cumulative_fee_amount REAL DEFAULT 0.0,
                cumulative_slippage_amount REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS execution_fill_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                fill_quantity REAL NOT NULL,
                intended_price REAL NOT NULL,
                broker_accepted_price REAL,
                fill_price REAL NOT NULL,
                fee_amount REAL DEFAULT 0.0,
                slippage_amount REAL DEFAULT 0.0,
                filled_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tax_lots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                opening_order_id TEXT,
                opened_at TEXT NOT NULL,
                original_quantity REAL NOT NULL,
                remaining_quantity REAL NOT NULL,
                cost_basis_price REAL NOT NULL,
                status TEXT DEFAULT 'open',
                closing_order_id TEXT,
                closed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS tax_lot_realizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lot_id INTEGER,
                symbol TEXT NOT NULL,
                sell_order_id TEXT,
                quantity REAL NOT NULL,
                opening_price REAL,
                closing_price REAL NOT NULL,
                fee_amount REAL DEFAULT 0.0,
                slippage_amount REAL DEFAULT 0.0,
                realized_pnl REAL,
                opened_at TEXT,
                closed_at TEXT NOT NULL,
                FOREIGN KEY(lot_id) REFERENCES tax_lots(id)
            );

            CREATE TABLE IF NOT EXISTS equity_curve (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                account_value REAL NOT NULL,
                cash REAL DEFAULT 0.0,
                positions_json TEXT DEFAULT '{}',
                market_date TEXT
            );

            CREATE TABLE IF NOT EXISTS position_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                value REAL NOT NULL
            );

            INSERT OR IGNORE INTO risk_state (id) VALUES (1);
        """)
        self._ensure_column(conn, "equity_curve", "market_date", "TEXT")
        self._ensure_column(conn, "order_history", "broker_accepted_price", "REAL")
        self._ensure_column(conn, "order_history", "avg_fill_price", "REAL")
        self._ensure_column(
            conn, "order_history", "cumulative_fee_amount", "REAL DEFAULT 0.0"
        )
        self._ensure_column(
            conn, "order_history", "cumulative_slippage_amount", "REAL DEFAULT 0.0"
        )
        self._ensure_column(
            conn, "risk_state", "rule_violation_count", "INTEGER DEFAULT 0"
        )
        conn.commit()

        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_order_history_order_id ON order_history(order_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_order_history_status ON order_history(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_tax_lots_symbol_status ON tax_lots(symbol, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_equity_curve_market_date ON equity_curve(market_date)"
        )
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_order_history_order_id_unique ON order_history(order_id)"
            )
        except sqlite3.IntegrityError:
            logger.warning(
                "Duplicate order_id values found in order_history; skipping UNIQUE index"
            )
        conn.commit()

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        # Validate table and column names to prevent SQL injection
        # Only allow alphanumeric + underscores (standard SQL identifiers)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table_name):
            raise ValueError(f"Invalid table name: {table_name}")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", column_name):
            raise ValueError(f"Invalid column name: {column_name}")

        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def load_risk_state(self) -> PersistentRiskState:
        conn = self._connect()
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
        with self._write_lock:
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

    # --- Orders ---

    def record_order(self, record: OrderRecord) -> int:
        conn = self._connect()
        normalized_status = _normalize_order_status(record.status) or "submitted"
        filled_at = record.filled_at
        if filled_at is None and _is_final_order_status(normalized_status):
            filled_at = _utc_now_iso()
        order_id = (
            None if record.order_id is None else str(record.order_id).strip() or None
        )
        with self._write_lock:
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
                    order_id,
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
        with self._write_lock:
            conn.execute("BEGIN")
            try:
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

    def get_pending_orders(self) -> list[OrderRecord]:
        conn = self._connect()
        status_placeholders = ", ".join("?" for _ in _PENDING_ORDER_STATUSES)
        rows = conn.execute(
            f"SELECT * FROM order_history WHERE status IN ({status_placeholders}) ORDER BY id",
            _PENDING_ORDER_STATUSES,
        ).fetchall()
        return [_order_record_from_row(r) for r in rows]

    def get_recent_orders(self, limit: int = 50) -> list[OrderRecord]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM order_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_order_record_from_row(r) for r in rows]

    def get_execution_fills(
        self,
        order_id: str | None = None,
        symbol: str | None = None,
        limit: int | None = None,
    ) -> list[ExecutionFillRecord]:
        conn = self._connect()
        query = "SELECT * FROM execution_fill_ledger"
        conditions: list[str] = []
        params: list[object] = []
        if order_id is not None:
            conditions.append("order_id = ?")
            params.append(str(order_id))
        if symbol is not None:
            conditions.append("symbol = ?")
            params.append(symbol)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [_execution_fill_record_from_row(r) for r in rows]

    def get_open_tax_lots(self, symbol: str | None = None) -> list[TaxLotRecord]:
        conn = self._connect()
        query = (
            "SELECT * FROM tax_lots WHERE remaining_quantity > 0.0 AND status = 'open'"
        )
        params: list[object] = []
        if symbol is not None:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY opened_at, id"
        rows = conn.execute(query, params).fetchall()
        return [_tax_lot_record_from_row(r) for r in rows]

    def get_tax_lot_realizations(
        self, symbol: str | None = None, limit: int | None = None
    ) -> list[TaxLotRealizationRecord]:
        conn = self._connect()
        query = "SELECT * FROM tax_lot_realizations"
        params: list[object] = []
        if symbol is not None:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [_tax_lot_realization_from_row(r) for r in rows]

    # --- Equity Curve ---

    def record_equity(
        self,
        account_value: float,
        cash: float,
        positions: dict[str, float],
        market_date: str | None = None,
    ) -> None:
        conn = self._connect()
        with self._write_lock:
            conn.execute(
                """
                INSERT INTO equity_curve (timestamp, account_value, cash, positions_json, market_date)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    _utc_now_iso(),
                    account_value,
                    cash,
                    json.dumps(positions),
                    market_date,
                ),
            )
            conn.commit()

    def get_equity_curve(self, since: str | None = None) -> list[EquitySnapshot]:
        conn = self._connect()
        if since:
            rows = conn.execute(
                "SELECT * FROM equity_curve WHERE timestamp >= ? ORDER BY id", (since,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM equity_curve ORDER BY id").fetchall()
        return [_equity_snapshot_from_row(r) for r in rows]

    def get_latest_equity_before_market_date(
        self, market_date: str
    ) -> EquitySnapshot | None:
        conn = self._connect()
        row = conn.execute(
            """
            SELECT *
            FROM equity_curve
            WHERE COALESCE(market_date, substr(timestamp, 1, 10)) < ?
            ORDER BY COALESCE(market_date, substr(timestamp, 1, 10)) DESC, id DESC
            LIMIT 1
            """,
            (market_date,),
        ).fetchone()
        if row is None:
            return None
        return _equity_snapshot_from_row(row)

    def load_equity_history(self, limit: int = 1000) -> list[EquitySnapshot]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM equity_curve ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_equity_snapshot_from_row(r) for r in reversed(rows)]

    def get_equity_at_month_start(self, market_date: str) -> EquitySnapshot | None:
        """Get equity snapshot at or just before the first day of the month containing market_date."""
        parts = market_date.split("-")
        if len(parts) < 2:
            return None
        month_start = f"{parts[0]}-{parts[1]}-01"
        conn = self._connect()
        row = conn.execute(
            """
            SELECT *
            FROM equity_curve
            WHERE COALESCE(market_date, substr(timestamp, 1, 10)) <= ?
            ORDER BY COALESCE(market_date, substr(timestamp, 1, 10)) DESC, id DESC
            LIMIT 1
            """,
            (month_start,),
        ).fetchone()
        if row is None:
            return None
        return _equity_snapshot_from_row(row)

    def get_recent_realizations(self, n: int) -> list[TaxLotRealizationRecord]:
        """Get the N most recent tax lot realizations, newest first."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM tax_lot_realizations ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [_tax_lot_realization_from_row(r) for r in rows]

    def load_recent_orders(self, limit: int = 50) -> list[OrderRecord]:
        """Load recent orders (alias for get_recent_orders)."""
        return self.get_recent_orders(limit)

    def summarize_execution_activity(
        self, symbol: str | None = None
    ) -> ExecutionAuditSummary:
        conn = self._connect()

        order_where = ""
        order_params: list[object] = []
        if symbol is not None:
            order_where = " WHERE symbol = ?"
            order_params.append(symbol)

        order_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM order_history{order_where}",
                order_params,
            ).fetchone()[0]
            or 0
        )

        pending_where_parts = [
            f"status IN ({', '.join('?' for _ in _PENDING_ORDER_STATUSES)})"
        ]
        pending_params: list[object] = [*_PENDING_ORDER_STATUSES]
        if symbol is not None:
            pending_where_parts.append("symbol = ?")
            pending_params.append(symbol)
        pending_order_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM order_history WHERE {' AND '.join(pending_where_parts)}",
                pending_params,
            ).fetchone()[0]
            or 0
        )

        fill_where = ""
        fill_params: list[object] = []
        if symbol is not None:
            fill_where = " WHERE symbol = ?"
            fill_params.append(symbol)
        fill_row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS fill_count,
                COALESCE(SUM(fee_amount), 0.0) AS total_fees,
                COALESCE(SUM(slippage_amount), 0.0) AS total_slippage,
                COALESCE(SUM(CASE WHEN UPPER(side) LIKE '%BUY%' THEN 1 ELSE 0 END), 0) AS buy_fill_count,
                COALESCE(SUM(CASE WHEN UPPER(side) LIKE '%SELL%' THEN 1 ELSE 0 END), 0) AS sell_fill_count,
                MAX(filled_at) AS last_fill_at
            FROM execution_fill_ledger{fill_where}
            """,
            fill_params,
        ).fetchone()

        realization_where = ""
        realization_params: list[object] = []
        if symbol is not None:
            realization_where = " WHERE symbol = ?"
            realization_params.append(symbol)
        realization_row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS realization_count,
                COALESCE(SUM(realized_pnl), 0.0) AS realized_pnl,
                MAX(closed_at) AS last_realization_at
            FROM tax_lot_realizations{realization_where}
            """,
            realization_params,
        ).fetchone()

        open_lot_where_parts = ["remaining_quantity > 0.0", "status = 'open'"]
        open_lot_params: list[object] = []
        if symbol is not None:
            open_lot_where_parts.append("symbol = ?")
            open_lot_params.append(symbol)
        open_lot_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM tax_lots WHERE {' AND '.join(open_lot_where_parts)}",
                open_lot_params,
            ).fetchone()[0]
            or 0
        )

        return ExecutionAuditSummary(
            order_count=order_count,
            pending_order_count=pending_order_count,
            fill_count=int(fill_row["fill_count"] or 0),
            buy_fill_count=int(fill_row["buy_fill_count"] or 0),
            sell_fill_count=int(fill_row["sell_fill_count"] or 0),
            realization_count=int(realization_row["realization_count"] or 0),
            open_lot_count=open_lot_count,
            total_fees=float(fill_row["total_fees"] or 0.0),
            total_slippage=float(fill_row["total_slippage"] or 0.0),
            realized_pnl=float(realization_row["realized_pnl"] or 0.0),
            last_fill_at=fill_row["last_fill_at"],
            last_realization_at=realization_row["last_realization_at"],
        )

    # --- Position Log ---

    def record_positions(
        self, positions: dict[str, float], prices: dict[str, float]
    ) -> None:
        conn = self._connect()
        now = _utc_now_iso()
        with self._write_lock:
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

    # --- Housekeeping ---

    def cleanup_old_equity(self, keep_days: int = 365) -> int:
        conn = self._connect()
        with self._write_lock:
            cursor = conn.execute(
                "DELETE FROM equity_curve WHERE timestamp < datetime('now', ?)",
                (f"-{keep_days} days",),
            )
            conn.commit()
            return cursor.rowcount

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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _normalize_side(side: object) -> str:
    normalized = str(side or "").strip().upper()
    if "SELL" in normalized:
        return "SELL"
    if "BUY" in normalized:
        return "BUY"
    return normalized


def _proportional_share(
    total_amount: float, partial_quantity: float, total_quantity: float
) -> float:
    if total_quantity <= 0.0:
        return 0.0
    return float(total_amount) * (float(partial_quantity) / float(total_quantity))


def _order_record_from_row(row: sqlite3.Row) -> OrderRecord:
    return OrderRecord(
        order_id=row["order_id"],
        symbol=row["symbol"],
        side=row["side"],
        quantity=row["quantity"],
        price=row["price"],
        status=row["status"],
        reason=row["reason"],
        filled_quantity=row["filled_quantity"],
        submitted_at=row["submitted_at"],
        filled_at=row["filled_at"],
        broker_accepted_price=row["broker_accepted_price"],
        avg_fill_price=row["avg_fill_price"],
        cumulative_fee_amount=row["cumulative_fee_amount"] or 0.0,
        cumulative_slippage_amount=row["cumulative_slippage_amount"] or 0.0,
    )


def _execution_fill_record_from_row(row: sqlite3.Row) -> ExecutionFillRecord:
    return ExecutionFillRecord(
        order_id=row["order_id"],
        symbol=row["symbol"],
        side=row["side"],
        fill_quantity=row["fill_quantity"],
        intended_price=row["intended_price"],
        broker_accepted_price=row["broker_accepted_price"],
        fill_price=row["fill_price"],
        fee_amount=row["fee_amount"],
        slippage_amount=row["slippage_amount"],
        filled_at=row["filled_at"],
    )


def _tax_lot_record_from_row(row: sqlite3.Row) -> TaxLotRecord:
    return TaxLotRecord(
        lot_id=row["id"],
        symbol=row["symbol"],
        opening_order_id=row["opening_order_id"],
        opened_at=row["opened_at"],
        original_quantity=row["original_quantity"],
        remaining_quantity=row["remaining_quantity"],
        cost_basis_price=row["cost_basis_price"],
        status=row["status"],
        closing_order_id=row["closing_order_id"],
        closed_at=row["closed_at"],
    )


def _tax_lot_realization_from_row(row: sqlite3.Row) -> TaxLotRealizationRecord:
    return TaxLotRealizationRecord(
        realization_id=row["id"],
        lot_id=row["lot_id"],
        symbol=row["symbol"],
        sell_order_id=row["sell_order_id"],
        quantity=row["quantity"],
        opening_price=row["opening_price"],
        closing_price=row["closing_price"],
        fee_amount=row["fee_amount"],
        slippage_amount=row["slippage_amount"],
        realized_pnl=row["realized_pnl"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
    )


def _equity_snapshot_from_row(row: sqlite3.Row) -> EquitySnapshot:
    return EquitySnapshot(
        timestamp=row["timestamp"],
        account_value=row["account_value"],
        cash=row["cash"],
        positions_json=row["positions_json"],
        market_date=row["market_date"],
    )
