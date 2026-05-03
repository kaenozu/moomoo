"""Local simulator utility functions.

Purpose: Constants and helpers for the local PaperSimulator used in
         paper-trading cycles (state paths, position frame building,
         state reset, and order sync).
Related: orchestrator/cycle.py, paper_simulator.py, state.py.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_LOCAL_SIM_PATH = Path.home() / ".moomoo_bot" / "paper-sim-state.json"
_LOCAL_SIM_STATE_DB_PATH = Path.home() / ".moomoo_bot" / "paper-sim-state.db"


def _build_position_frame_from_sim(sim) -> "pd.DataFrame":
    """Build a position DataFrame compatible with row_utils from a PaperSimulator."""
    rows = [
        {
            "code": pos.symbol,
            "qty": pos.quantity,
            "cost_price": pos.avg_cost,
        }
        for pos in sim.positions.values()
        if pos.quantity > 0.0
    ]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["code", "qty", "cost_price"])


def _reset_local_sim_state_store(state_store) -> None:
    """Clear persisted local-sim risk/equity/order state when starting a fresh simulator."""
    conn_getter = getattr(state_store, "_connect", None)
    lock = getattr(state_store, "_lock", None)
    if not callable(conn_getter) or lock is None:
        return

    db_conn = conn_getter()
    tables = [
        "risk_state",
        "order_history",
        "execution_fill_ledger",
        "tax_lots",
        "tax_lot_realizations",
        "equity_curve",
        "position_log",
    ]

    with lock:
        try:
            db_conn.execute("BEGIN IMMEDIATE")
            for table in tables:
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table):
                    raise ValueError(f"Invalid table name in reset: {table}")
                db_conn.execute(f"DELETE FROM {table}")
            db_conn.commit()
            db_conn.execute("VACUUM")
        except Exception:
            db_conn.rollback()
            raise


def _sync_orders_to_local_simulator(
    orders: list,
    prices: dict[str, float],
    local_sim_path: Path | None = None,
) -> int:
    """Write executed/planned orders into the local PaperSimulator so the Streamlit UI reflects them."""
    if not orders:
        return 0
    try:
        from moomoo_bot.paper_simulator import PaperSimulator

        local_sim_path = local_sim_path or _LOCAL_SIM_PATH
        sim = PaperSimulator.load(state_path=local_sim_path, initial_cash=100_000.0)
        applied_count = 0
        for order in orders:
            symbol = str(order.symbol)
            side = str(order.side)  # TrdSide.BUY → 'BUY'
            qty = float(order.quantity)
            px = float(prices.get(symbol, order.price))
            if qty > 0.0:
                sim.place_market_order(symbol=symbol, side=side, quantity=qty, price=px)
                applied_count += 1
        sim.mark_to_market(prices)
        sim.save()
        logger.info("Synced %d orders to local simulator (%s)", applied_count, local_sim_path)
        return applied_count
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to sync orders to local simulator: %s", exc)
        return 0
