"""Paper repair module.

Purpose: Flatten paper positions and optionally clear the local paper state.
Related: orchestrator/__init__.py, orchestrator/helpers.py.
"""

from __future__ import annotations

import logging
import shutil
from time import sleep

from moomoo import Session, TrdEnv, TrdSide

from moomoo_bot.cli_helpers import (
    fetch_market_state as _fetch_market_state,
    is_regular_market_open as _is_regular_market_open,
)
from moomoo_bot.cli_render import (
    console,
    render_order_response,
    render_risk_orders,
)
from moomoo_bot.kill_switch import is_kill_switch_active as _is_kill_switch_active
from moomoo_bot.notify import notify_kill_switch
from moomoo_bot.paper import PaperOrderInstruction
from moomoo_bot.quantities import round_quantity_toward_zero
from moomoo_bot.row_utils import row_text as _row_text, row_float as _row_float
from moomoo_bot.state import StateStore

from moomoo_bot.orchestrator.helpers import (
    kill_switch_message,
    signed_position_quantities,
    snapshot_latest_prices,
    webhook_str,
)

logger = logging.getLogger(__name__)

_POSITION_CLEAR_MAX_RETRIES = 15
_POSITION_CLEAR_INTERVAL_SEC = 1.0


def _wait_for_positions_cleared(
    trade_client,
    state_store,
    *,
    max_retries: int = _POSITION_CLEAR_MAX_RETRIES,
    interval_sec: float = _POSITION_CLEAR_INTERVAL_SEC,
) -> bool:
    """Poll until broker reports no positions, then clear state files.

    Returns True if positions were confirmed cleared, False otherwise.
    """
    for _ in range(max_retries):
        refreshed_positions = signed_position_quantities(
            trade_client.get_position_frame()
        )
        if not refreshed_positions:
            _clear_state_files(state_store)
            console.print("Local paper state cleared.")
            return True
        sleep(interval_sec)
    console.print("Paper positions still remain; local state kept.")
    return False


def _build_paper_repair_orders(
    position_frame,
    latest_prices: dict[str, float],
    settings,
    market_open: bool,
) -> list[PaperOrderInstruction]:
    orders: list[PaperOrderInstruction] = []
    for _, row in position_frame.iterrows():
        symbol = _row_text(row, "code", "symbol", "stock_code", "ticker")
        if not symbol:
            continue
        quantity = _row_float(row, "qty", "position_qty", "holding_qty", "can_use_qty")
        if quantity is None or quantity == 0.0:
            continue
        if symbol not in latest_prices:
            raise ValueError(f"missing latest price for repair symbol {symbol}")

        normalized_quantity = round_quantity_toward_zero(
            abs(quantity), precision=settings.fractional_share_precision
        )
        if normalized_quantity <= 0.0:
            continue

        position_side = _row_text(row, "position_side").upper()
        # Broker payloads can contain signed quantity noise in some edge cases.
        # Prefer explicit side when present; only fall back to quantity sign.
        if position_side == "SHORT":
            is_short = True
        elif position_side == "LONG":
            is_short = False
        else:
            is_short = quantity < 0.0
        orders.append(
            PaperOrderInstruction(
                symbol=symbol,
                side=TrdSide.BUY if is_short else TrdSide.SELL,
                quantity=normalized_quantity,
                price=latest_prices[symbol],
                reason=f"paper_repair:{'cover_short' if is_short else 'liquidate'}:{symbol}",
                session=Session.NONE if market_open else Session.ETH,
                fill_outside_rth=not market_open,
            )
        )

    return sorted(
        orders,
        key=lambda instruction: (instruction.side != TrdSide.SELL, instruction.symbol),
    )


def _clear_state_files(state_store: StateStore) -> None:
    db_path = state_store.db_path
    if db_path.exists():
        backup_path = db_path.with_suffix(".db.bak")
        shutil.copy2(str(db_path), str(backup_path))
        logger.warning("Backing up and clearing state data: %s", db_path)
    
    conn = state_store._connect()
    with state_store._lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [row[0] for row in cursor.fetchall()]
            for table in tables:
                conn.execute(f"DELETE FROM {table}")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
            conn.execute("VACUUM")
        except Exception:
            conn.rollback()
            raise
    state_store.close()


def run_paper_repair(
    *,
    settings,
    benchmark_symbol: str,
    quote_client=None,
    trade_client=None,
    state_store=None,
    clear_local_state: bool = True,
) -> bool:
    import moomoo_bot.orchestrator as _orch
    from moomoo_bot.broker import MoomooOpenDClient
    from moomoo_bot.broker.paper import MoomooPaperTradeClient

    if _is_kill_switch_active():
        message = kill_switch_message()
        logger.warning(message)
        console.print(message)
        notify_kill_switch(webhook_str(settings))
        return False

    owns_quote_client = quote_client is None
    owns_trade_client = trade_client is None
    owns_state_store = state_store is None
    if owns_quote_client:
        quote_client = MoomooOpenDClient(
            host=settings.opend_host, port=settings.opend_port
        )
    if owns_trade_client:
        trade_client = MoomooPaperTradeClient(
            host=settings.opend_host, port=settings.opend_port, trd_env=TrdEnv.SIMULATE
        )
    if owns_state_store:
        state_store = StateStore(
            db_path=settings.state_db_path,
            execution_mode=settings.execution_mode,
        )

    try:
        position_frame = trade_client.get_position_frame()
        signed_positions = signed_position_quantities(position_frame)
        if not signed_positions:
            console.print("No open paper positions found.")
            if clear_local_state and owns_state_store:
                _clear_state_files(state_store)
                console.print("Local paper state cleared.")
            return True

        market_state = _fetch_market_state(quote_client, benchmark_symbol)
        market_open = _is_regular_market_open(market_state)
        latest_prices = snapshot_latest_prices(
            quote_client, list(signed_positions.keys())
        )
        repair_orders = _build_paper_repair_orders(
            position_frame,
            latest_prices,
            settings,
            market_open,
        )
        if not repair_orders:
            console.print("No paper repair orders were required.")
            if clear_local_state and owns_state_store:
                _clear_state_files(state_store)
                console.print("Local paper state cleared.")
            return True

        render_risk_orders(repair_orders, signed_positions, "Paper Repair Orders")
        console.print("Submitting paper repair orders...")
        submitted_count = _orch._submit_orders_with_duplicate_guard(
            trade_client,
            repair_orders,
            "paper",
            render_order_response,
            state_store=state_store,
        )

        if clear_local_state:
            matching_active_order = False
            get_matching_active_order = getattr(
                trade_client, "get_matching_active_order", None
            )
            if callable(get_matching_active_order):
                matching_active_order = any(
                    get_matching_active_order(order, refresh_cache=True) is not None
                    for order in repair_orders
                )

            if submitted_count == 0:
                if not matching_active_order:
                    refreshed_positions = signed_position_quantities(
                        trade_client.get_position_frame()
                    )
                    if not refreshed_positions:
                        _clear_state_files(state_store)
                        console.print("Local paper state cleared.")
                    else:
                        console.print(
                            "Paper repair orders were not accepted; local state kept."
                        )
                        remaining_shorts = [
                            (symbol, qty)
                            for symbol, qty in refreshed_positions.items()
                            if qty < 0.0
                        ]
                        if remaining_shorts:
                            short_text = ", ".join(
                                f"{symbol}({qty:.3f})"
                                for symbol, qty in sorted(remaining_shorts)
                            )
                            console.print(
                                "Broker still reports short positions after repair rejection: "
                                f"{short_text}. Manual close or paper account reset may be required."
                            )
                else:
                    _wait_for_positions_cleared(trade_client, state_store)
            else:
                _wait_for_positions_cleared(trade_client, state_store)

        return True
    finally:
        if owns_quote_client:
            quote_client.close()
        if owns_trade_client:
            trade_client.close()
        if owns_state_store and not clear_local_state:
            state_store.close()
