"""Tests for state module."""

import pytest
from pathlib import Path
from datetime import datetime, timezone, date as datetime_date
import sqlite3

from moomoo_bot.state import StateStore, PersistentRiskState, OrderRecord, EquitySnapshot


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

    conn.close()


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


def test_close(state_store: StateStore):
    """Test closing the state store."""
    state_store.close()
    # Should not raise an error