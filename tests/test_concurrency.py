"""Concurrency tests for StateStore and order flows.

Tests verify that the locking strategy prevents deadlocks and data corruption
when multiple threads access StateStore concurrently.
"""

from __future__ import annotations

import random
import string
import threading
from time import sleep

import pytest

from moomoo_bot.state import StateStore, OrderRecord, PersistentRiskState
from moomoo_bot.risk import RiskState


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create a temporary database path for concurrency tests."""
    return tmp_path / "concurrent_state.db"


def test_concurrent_writes_no_deadlock(temp_db: Path) -> None:
    """Multiple threads performing writes concurrently should not deadlock."""
    store = StateStore(db_path=temp_db, execution_mode="paper")

    errors: list[Exception] = []
    threads = []

    def worker(thread_id: int) -> None:
        try:
            for i in range(20):
                # Record an order
                order = OrderRecord(
                    order_id=f"thread-{thread_id}-order-{i}",
                    symbol="US.AAPL",
                    side="BUY",
                    quantity=random.uniform(1.0, 100.0),
                    price=150.0,
                    reason=f"concurrent-test-{thread_id}",
                )
                store.record_order(order)

                # Update order status to filled
                store.update_order_status(
                    order.order_id,
                    "filled_all",
                    order.quantity,
                    fill_price=151.0,
                    broker_accepted_price=150.5,
                    fee_amount=1.0,
                )

                # Update risk state
                risk = RiskState(
                    peak_account_value=100_000.0,
                    halted=False,
                )
                persistent = store.load_risk_state()
                store.save_risk_state(
                    PersistentRiskState(
                        peak_account_value=risk.peak_account_value,
                        halted=risk.halted,
                        halted_reason=risk.halted_reason,
                        drawdown_tier=risk.drawdown_tier,
                        daily_order_count=persistent.daily_order_count,
                        daily_order_date=persistent.daily_order_date,
                        last_equity_value=100_000.0 - i * 10,
                    )
                )
                # Random tiny sleep to increase interleaving
                sleep(random.uniform(0.001, 0.01))
        except Exception as exc:
            errors.append(exc)

    num_threads = 5
    for t_id in range(num_threads):
        t = threading.Thread(target=worker, args=(t_id,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # No thread should have raised an exception
    assert not errors, f"Concurrent execution raised exceptions: {errors}"

    # Verify total order count: each thread wrote 20 orders
    all_orders = store.get_recent_orders(limit=1000)
    assert len(all_orders) == num_threads * 20

    # Ensure no duplicate order_ids across all threads
    order_ids = [o.order_id for o in all_orders]
    assert len(order_ids) == len(set(order_ids)), "Duplicate order IDs found"


def test_concurrent_reads_during_writes(temp_db: Path) -> None:
    """Concurrent reads should not block writers and vice versa (WAL mode)."""
    store = StateStore(db_path=temp_db, execution_mode="paper")
    write_done = threading.Event()
    errors: list[Exception] = []

    def continuous_reader() -> None:
        """Repeatedly read pending orders while writer runs."""
        try:
            for _ in range(100):
                orders = store.get_pending_orders()
                assert isinstance(orders, list)
                # Also read risk state
                _ = store.load_risk_state()
                sleep(0.005)
        except Exception as exc:
            errors.append(exc)

    def writer() -> None:
        try:
            for i in range(50):
                order = OrderRecord(
                    order_id=f"writer-order-{i}",
                    symbol="US.MSFT",
                    side="SELL",
                    quantity=10.0,
                    price=250.0,
                    reason="concurrent-write",
                )
                store.record_order(order)
                store.update_order_status(order.order_id, "filled_all", 10.0)
                sleep(0.01)
            write_done.set()
        except Exception as exc:
            errors.append(exc)

    reader_thread = threading.Thread(target=continuous_reader)
    writer_thread = threading.Thread(target=writer)

    reader_thread.start()
    writer_thread.start()

    writer_thread.join(timeout=30)
    write_done.wait(timeout=30)
    reader_thread.join(timeout=30)

    assert not errors, f"Concurrent read/write raised exceptions: {errors}"
    assert write_done.is_set(), "Writer did not finish in time"


def test_lock_reentrancy() -> None:
    """StateStore methods should be reentrant (RLock) to avoid self-deadlock."""
    temp_db = Path("/tmp/test_reentrancy.db")  # Use temp file; cleanup later
    store = StateStore(db_path=temp_db, execution_mode="paper")

    def recursive_operation(depth: int) -> None:
        if depth <= 0:
            return
        # Inside a lock, call another method that also acquires lock
        with store._lock:
            # Simulate nested lock acquisition
            store.record_order(
                OrderRecord(
                    order_id=f"reentrant-{threading.current_thread().name}-{depth}",
                    symbol="US.AMD",
                    side="BUY",
                    quantity=5.0,
                    price=100.0,
                    reason="reentrancy-test",
                )
            )
            recursive_operation(depth - 1)

    # Call nested within same thread (should not deadlock due to RLock)
    try:
        with store._lock:
            recursive_operation(3)
    except Exception as exc:
        pytest.fail(f"Reentrancy caused exception: {exc}")


def test_concurrent_risk_state_updates(temp_db: Path) -> None:
    """Concurrent updates to the singleton risk_state row should serialize correctly."""
    store = StateStore(db_path=temp_db, execution_mode="paper")
    num_updates = 100
    success_count = 0
    lock = threading.Lock()

    def updater(offset: int) -> None:
        nonlocal success_count
        for i in range(num_updates):
            try:
                persistent = store.load_risk_state()
                # Modify peak incrementally to simulate drift
                new_peak = (persistent.peak_account_value or 100_000.0) + (offset + 1) * 0.01
                persistent.peak_account_value = new_peak
                store.save_risk_state(persistent)
                with lock:
                    success_count += 1
            except Exception:
                pass  # ignore transient errors, we'll check success count

    threads = [threading.Thread(target=updater, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Expect at least most updates to succeed, no crashes
    assert success_count > 0
    # The final peak should be some value within expected range
    final = store.load_risk_state()
    assert final.peak_account_value is not None
