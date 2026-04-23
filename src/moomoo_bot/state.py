"""State persistence module.

Purpose: Persist trading state (risk, positions, orders, equity) to SQLite.
Related: orchestrator.py, risk.py, notify.py.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field

_DEFAULT_DB_DIR = Path.home() / ".moomoo_bot"
_DEFAULT_DB_NAME = "state.db"


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


@dataclass
class EquitySnapshot:
    timestamp: str = ""
    account_value: float = 0.0
    cash: float = 0.0
    positions_json: str = "{}"


class StateStore:
    """SQLite-backed state persistence for the trading bot."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            db_path = _DEFAULT_DB_DIR / _DEFAULT_DB_NAME
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_tables()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensure connection is closed."""
        self.close()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=10)
            self._conn.row_factory = sqlite3.Row
            # Only set WAL mode once, not on every connection
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

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
                filled_at TEXT
            );

            CREATE TABLE IF NOT EXISTS equity_curve (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                account_value REAL NOT NULL,
                cash REAL DEFAULT 0.0,
                positions_json TEXT DEFAULT '{}'
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
        conn.commit()

    # --- Risk State ---

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
        )

    def save_risk_state(self, state: PersistentRiskState) -> None:
        now = _utc_now_iso()
        conn = self._connect()
        conn.execute("""
            UPDATE risk_state SET
                peak_account_value = ?,
                halted = ?,
                halted_reason = ?,
                drawdown_tier = ?,
                daily_order_count = ?,
                daily_order_date = ?,
                last_equity_value = ?,
                updated_at = ?
            WHERE id = 1
        """, (
            state.peak_account_value,
            int(state.halted),
            state.halted_reason,
            state.drawdown_tier,
            state.daily_order_count,
            state.daily_order_date,
            state.last_equity_value,
            now,
        ))
        conn.commit()

    # --- Orders ---

    def record_order(self, record: OrderRecord) -> int:
        conn = self._connect()
        cursor = conn.execute("""
            INSERT INTO order_history
                (order_id, symbol, side, quantity, price, status, reason, filled_quantity, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.order_id,
            record.symbol,
            record.side,
            record.quantity,
            record.price,
            record.status,
            record.reason,
            record.filled_quantity,
            record.submitted_at or _utc_now_iso(),
        ))
        conn.commit()
        return cursor.lastrowid

    def update_order_status(self, order_id: str, status: str, filled_quantity: float) -> None:
        conn = self._connect()
        filled_at = _utc_now_iso() if status in ("filled_all", "filled_part", "cancelled") else None
        conn.execute("""
            UPDATE order_history SET status = ?, filled_quantity = ?, filled_at = COALESCE(?, filled_at)
            WHERE order_id = ?
        """, (status, filled_quantity, filled_at, order_id))
        conn.commit()

    def get_pending_orders(self) -> list[OrderRecord]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM order_history WHERE status IN ('submitted', 'submitting', 'partial') ORDER BY id"
        ).fetchall()
        return [
            OrderRecord(
                order_id=r["order_id"],
                symbol=r["symbol"],
                side=r["side"],
                quantity=r["quantity"],
                price=r["price"],
                status=r["status"],
                reason=r["reason"],
                filled_quantity=r["filled_quantity"],
                submitted_at=r["submitted_at"],
                filled_at=r["filled_at"],
            )
            for r in rows
        ]

    def get_recent_orders(self, limit: int = 50) -> list[OrderRecord]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM order_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            OrderRecord(
                order_id=r["order_id"],
                symbol=r["symbol"],
                side=r["side"],
                quantity=r["quantity"],
                price=r["price"],
                status=r["status"],
                reason=r["reason"],
                filled_quantity=r["filled_quantity"],
                submitted_at=r["submitted_at"],
                filled_at=r["filled_at"],
            )
            for r in rows
        ]

    # --- Equity Curve ---

    def record_equity(self, account_value: float, cash: float, positions: dict[str, float]) -> None:
        conn = self._connect()
        conn.execute("""
            INSERT INTO equity_curve (timestamp, account_value, cash, positions_json)
            VALUES (?, ?, ?, ?)
        """, (_utc_now_iso(), account_value, cash, json.dumps(positions)))
        conn.commit()

    def get_equity_curve(self, since: str | None = None) -> list[EquitySnapshot]:
        conn = self._connect()
        if since:
            rows = conn.execute(
                "SELECT * FROM equity_curve WHERE timestamp >= ? ORDER BY id", (since,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM equity_curve ORDER BY id").fetchall()
        return [
            EquitySnapshot(
                timestamp=r["timestamp"],
                account_value=r["account_value"],
                cash=r["cash"],
                positions_json=r["positions_json"],
            )
            for r in rows
        ]

    def load_equity_history(self, limit: int = 1000) -> list[EquitySnapshot]:
        """Load equity history for health check (P4)."""
        return self.get_equity_curve()

    def load_recent_orders(self, limit: int = 50) -> list[OrderRecord]:
        """Load recent orders (alias for get_recent_orders)."""
        return self.get_recent_orders(limit)

    # --- Position Log ---

    def record_positions(self, positions: dict[str, float], prices: dict[str, float]) -> None:
        conn = self._connect()
        now = _utc_now_iso()
        for symbol, qty in positions.items():
            price = prices.get(symbol, 0.0)
            conn.execute("""
                INSERT INTO position_log (timestamp, symbol, quantity, price, value)
                VALUES (?, ?, ?, ?, ?)
            """, (now, symbol, qty, price, qty * price))
        conn.commit()

    # --- Housekeeping ---

    def cleanup_old_equity(self, keep_days: int = 365) -> int:
        conn = self._connect()
        cursor = conn.execute(
            "DELETE FROM equity_curve WHERE timestamp < datetime('now', ?)",
            (f"-{keep_days} days",),
        )
        conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()