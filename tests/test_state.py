"""Tests for state module."""

import pytest
from pathlib import Path
from datetime import datetime, timezone, date as datetime_date
import sqlite3

from moomoo_bot.state import (
    ExecutionAuditSummary,
    StateStore,
    PersistentRiskState,
    OrderRecord,
    EquitySnapshot,
    resolve_state_db_path,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create a temporary database path."""
    return tmp_path / "test_state.db"


@pytest.fixture
def state_store(temp_db: Path) -> StateStore:
    """Create a StateStore instance with a temporary database."""
    return StateStore(db_path=temp_db)


def test_init_creates_tables(state_store: StateStore):
    """Test that initialization creates necessary tables."""
    conn = sqlite3.connect(state_store.db_path)
    cursor = conn.cursor()

    # Check risk_state table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='risk_state'")
    assert cursor.fetchone() is not None

    # Check order_history table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='order_history'")
    assert cursor.fetchone() is not None

    # Check equity_curve table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='equity_curve'")
    assert cursor.fetchone() is not None

    # Check execution ledger tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='execution_fill_ledger'")
    assert cursor.fetchone() is not None
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tax_lots'")
    assert cursor.fetchone() is not None
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tax_lot_realizations'")
    assert cursor.fetchone() is not None

    conn.close()


def test_resolve_state_db_path_separates_paper_and_live_defaults() -> None:
    paper_path = resolve_state_db_path(execution_mode="paper")
    live_path = resolve_state_db_path(execution_mode="live")

    assert paper_path.name == "paper-state.db"
    assert live_path.name == "live-state.db"
    assert paper_path != live_path


def test_save_and_load_risk_state(state_store: StateStore):
    """Test saving and loading risk state."""
    original = PersistentRiskState(
        peak_account_value=100000.0,
        halted=False,
        halted_reason=None,
        drawdown_tier=0,
        last_equity_value=100000.0,
        daily_order_count=0,
        daily_order_date="2024-01-01",
    )

    state_store.save_risk_state(original)
    loaded = state_store.load_risk_state()

    assert loaded.peak_account_value == 100000.0
    assert loaded.halted is False
    assert loaded.halted_reason is None
    assert loaded.drawdown_tier == 0
    assert loaded.last_equity_value == 100000.0


def test_record_order(state_store: StateStore):
    """Test recording an order."""
    order = OrderRecord(
        order_id="12345",
        symbol="US.AAPL",
        side="BUY",
        quantity=10.0,
        price=150.0,
        reason="test",
    )

    state_store.record_order(order)
    orders = state_store.load_recent_orders(limit=1)

    assert len(orders) == 1
    assert orders[0].order_id == "12345"
    assert orders[0].symbol == "US.AAPL"


def test_record_order_normalizes_terminal_status(state_store: StateStore):
    """Test that terminal order statuses are normalized and marked complete."""
    state_store.record_order(
        OrderRecord(
            order_id=12345,
            symbol="US.AAPL",
            side="BUY",
            quantity=10.0,
            price=150.0,
            status="FILLED",
            reason="test",
        )
    )

    order = state_store.load_recent_orders(limit=1)[0]

    assert order.order_id == "12345"
    assert order.status == "filled_all"
    assert order.filled_at is not None


def test_get_pending_orders_includes_transitional_statuses(state_store: StateStore):
    """Test that in-flight order statuses stay pending until they are finalized."""
    state_store.record_order(
        OrderRecord(
            order_id="submitted-1",
            symbol="US.AAPL",
            side="BUY",
            quantity=10.0,
            price=150.0,
            status="submitted",
            reason="test",
        )
    )
    state_store.record_order(
        OrderRecord(
            order_id="submitting-1",
            symbol="US.MSFT",
            side="BUY",
            quantity=5.0,
            price=250.0,
            status="submitting",
            reason="test",
        )
    )
    state_store.record_order(
        OrderRecord(
            order_id="partial-1",
            symbol="US.NVDA",
            side="SELL",
            quantity=2.0,
            price=300.0,
            status="partial",
            reason="test",
        )
    )
    state_store.record_order(
        OrderRecord(
            order_id="filled-part-1",
            symbol="US.TSLA",
            side="BUY",
            quantity=1.0,
            price=200.0,
            status="filled_part",
            reason="test",
        )
    )
    state_store.record_order(
        OrderRecord(
            order_id="done-1",
            symbol="US.VT",
            side="BUY",
            quantity=1.0,
            price=100.0,
            status="filled_all",
            reason="test",
        )
    )

    assert [order.order_id for order in state_store.get_pending_orders()] == [
        "submitted-1",
        "submitting-1",
        "partial-1",
        "filled-part-1",
    ]

    state_store.update_order_status("submitted-1", "FILLED_ALL", 10.0)

    assert [order.order_id for order in state_store.get_pending_orders()] == [
        "submitting-1",
        "partial-1",
        "filled-part-1",
    ]

    updated_order = state_store.load_recent_orders(limit=5)[-1]
    assert updated_order.order_id == "submitted-1"
    assert updated_order.status == "filled_all"
    assert updated_order.filled_at is not None


def test_record_equity(state_store: StateStore):
    """Test recording equity snapshots."""
    state_store.record_equity(
        account_value=100000.0,
        cash=10000.0,
        positions={"US.AAPL": 90000.0},
    )
    history = state_store.load_equity_history()

    assert len(history) == 1
    assert history[0].account_value == 100000.0


def test_get_latest_equity_before_market_date(state_store: StateStore):
    """Test loading the latest prior-market-date equity snapshot."""
    state_store.record_equity(
        account_value=100000.0,
        cash=10000.0,
        positions={"US.AAPL": 90000.0},
        market_date="2025-01-02",
    )
    state_store.record_equity(
        account_value=98000.0,
        cash=8000.0,
        positions={"US.AAPL": 90000.0},
        market_date="2025-01-03",
    )

    snapshot = state_store.get_latest_equity_before_market_date("2025-01-03")

    assert snapshot is not None
    assert snapshot.account_value == 100000.0
    assert snapshot.market_date == "2025-01-02"


def test_update_order_status_records_execution_fill_and_open_tax_lots(state_store: StateStore):
    state_store.record_order(
        OrderRecord(
            order_id="buy-1",
            symbol="US.AAPL",
            side="BUY",
            quantity=10.0,
            price=100.0,
            status="submitted",
            reason="test",
        )
    )

    state_store.update_order_status(
        "buy-1",
        "filled_part",
        4.0,
        fill_price=101.5,
        broker_accepted_price=100.5,
        fee_amount=1.2,
    )
    state_store.update_order_status(
        "buy-1",
        "filled_all",
        10.0,
        fill_price=101.8,
        broker_accepted_price=100.5,
        fee_amount=2.4,
    )

    fills = state_store.get_execution_fills(order_id="buy-1")
    recent_order = state_store.load_recent_orders(limit=1)[0]
    open_lots = state_store.get_open_tax_lots(symbol="US.AAPL")

    assert [fill.fill_quantity for fill in fills] == [4.0, 6.0]
    assert fills[0].fill_price == pytest.approx(101.5)
    assert fills[1].fill_price == pytest.approx(102.0)
    assert fills[0].slippage_amount == pytest.approx(6.0)
    assert fills[1].slippage_amount == pytest.approx(12.0)
    assert fills[0].fee_amount == pytest.approx(1.2)
    assert fills[1].fee_amount == pytest.approx(1.2)

    assert recent_order.avg_fill_price == pytest.approx(101.8)
    assert recent_order.cumulative_fee_amount == pytest.approx(2.4)
    assert recent_order.cumulative_slippage_amount == pytest.approx(18.0)
    assert recent_order.broker_accepted_price == pytest.approx(100.5)

    assert [lot.remaining_quantity for lot in open_lots] == [4.0, 6.0]
    assert [lot.cost_basis_price for lot in open_lots] == [pytest.approx(101.8), pytest.approx(102.2)]


def test_sell_fills_consume_tax_lots_fifo_and_record_realizations(state_store: StateStore):
    state_store.record_order(
        OrderRecord(
            order_id="buy-1",
            symbol="US.AAPL",
            side="BUY",
            quantity=10.0,
            price=100.0,
            status="submitted",
            reason="test",
        )
    )
    state_store.update_order_status(
        "buy-1",
        "filled_all",
        10.0,
        fill_price=101.0,
        broker_accepted_price=100.0,
        fee_amount=1.0,
    )

    state_store.record_order(
        OrderRecord(
            order_id="sell-1",
            symbol="US.AAPL",
            side="SELL",
            quantity=3.0,
            price=110.0,
            status="submitted",
            reason="rebalance",
        )
    )
    state_store.update_order_status(
        "sell-1",
        "filled_all",
        3.0,
        fill_price=110.0,
        broker_accepted_price=110.0,
        fee_amount=0.6,
    )

    open_lots = state_store.get_open_tax_lots(symbol="US.AAPL")
    realizations = state_store.get_tax_lot_realizations(symbol="US.AAPL")
    sell_fills = state_store.get_execution_fills(order_id="sell-1")

    assert len(sell_fills) == 1
    assert sell_fills[0].slippage_amount == pytest.approx(0.0)
    assert len(realizations) == 1
    assert realizations[0].quantity == pytest.approx(3.0)
    assert realizations[0].opening_price == pytest.approx(101.1)
    assert realizations[0].closing_price == pytest.approx(110.0)
    assert realizations[0].fee_amount == pytest.approx(0.6)
    assert realizations[0].realized_pnl == pytest.approx(26.1)

    assert len(open_lots) == 1
    assert open_lots[0].remaining_quantity == pytest.approx(7.0)


def test_summarize_execution_activity_rolls_up_fills_realizations_and_pending_orders(
    state_store: StateStore,
):
    state_store.record_order(
        OrderRecord(
            order_id="buy-1",
            symbol="US.AAPL",
            side="BUY",
            quantity=10.0,
            price=100.0,
            status="submitted",
            reason="test",
        )
    )
    state_store.update_order_status(
        "buy-1",
        "filled_all",
        10.0,
        fill_price=101.0,
        broker_accepted_price=100.0,
        fee_amount=1.0,
        filled_at="2025-01-02T15:30:00+00:00",
    )

    state_store.record_order(
        OrderRecord(
            order_id="sell-1",
            symbol="US.AAPL",
            side="SELL",
            quantity=3.0,
            price=110.0,
            status="submitted",
            reason="rebalance",
        )
    )
    state_store.update_order_status(
        "sell-1",
        "filled_all",
        3.0,
        fill_price=110.0,
        broker_accepted_price=110.0,
        fee_amount=0.6,
        filled_at="2025-01-03T15:30:00+00:00",
    )

    state_store.record_order(
        OrderRecord(
            order_id="pending-1",
            symbol="US.AAPL",
            side="BUY",
            quantity=1.0,
            price=99.0,
            status="submitted",
            reason="pending",
        )
    )

    summary = state_store.summarize_execution_activity(symbol="US.AAPL")

    assert isinstance(summary, ExecutionAuditSummary)
    assert summary.order_count == 3
    assert summary.pending_order_count == 1
    assert summary.fill_count == 2
    assert summary.buy_fill_count == 1
    assert summary.sell_fill_count == 1
    assert summary.realization_count == 1
    assert summary.open_lot_count == 1
    assert summary.total_fees == pytest.approx(1.6)
    assert summary.total_slippage == pytest.approx(10.0)
    assert summary.realized_pnl == pytest.approx(26.1)
    assert summary.last_fill_at == "2025-01-03T15:30:00+00:00"
    assert summary.last_realization_at == "2025-01-03T15:30:00+00:00"



def test_close(state_store: StateStore):
    """Test closing the state store."""
    state_store.close()
    # Should not raise an error