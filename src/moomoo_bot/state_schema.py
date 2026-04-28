"""State schema management module.

Purpose: Database schema creation, migration, and order status normalization.
         Extracted from state.py to keep file sizes manageable.
Related: state.py, state_types.py.
"""

from __future__ import annotations

import logging
import re
import sqlite3

logger = logging.getLogger(__name__)

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


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
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


def _ensure_tables(conn: sqlite3.Connection) -> None:
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
    _ensure_column(conn, "equity_curve", "market_date", "TEXT")
    _ensure_column(conn, "order_history", "broker_accepted_price", "REAL")
    _ensure_column(conn, "order_history", "avg_fill_price", "REAL")
    _ensure_column(
        conn, "order_history", "cumulative_fee_amount", "REAL DEFAULT 0.0"
    )
    _ensure_column(
        conn, "order_history", "cumulative_slippage_amount", "REAL DEFAULT 0.0"
    )
    _ensure_column(
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
