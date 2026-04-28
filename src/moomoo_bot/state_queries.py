"""State queries module — read-only query methods for trading state.

Purpose: Read query methods extracted from StateStore as a mixin class.
         Keeps state.py focused on write operations and lifecycle management.
Related: state.py, state_ledger.py, state_types.py, state_schema.py.
"""

from __future__ import annotations

import logging

from moomoo_bot.state_types import (
    EquitySnapshot,
    ExecutionAuditSummary,
    ExecutionFillRecord,
    OrderRecord,
    TaxLotRealizationRecord,
    TaxLotRecord,
    _equity_snapshot_from_row,
    _execution_fill_record_from_row,
    _order_record_from_row,
    _tax_lot_realization_from_row,
    _tax_lot_record_from_row,
)
from moomoo_bot.state_schema import _PENDING_ORDER_STATUSES

logger = logging.getLogger(__name__)


class _QueryMixin:
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
