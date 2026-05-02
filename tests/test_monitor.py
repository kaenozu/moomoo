"""Tests for orchestrator/monitor.py - run_auto_monitor additional edge cases."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pytest

from moomoo_bot.config import Settings
from moomoo_bot.state import EquitySnapshot, OrderRecord, PersistentRiskState


# ---------------------------------------------------------------------------
# Minimal fakes (copied from test_orchestrator pattern)
# ---------------------------------------------------------------------------

@dataclass
class FakeQuoteClient:
    fetch_price_panel_calls: int = 0

    def fetch_price_panel(
        self,
        selected_symbols,
        benchmark_label,
        history_days=2200,
        include_benchmark_in_prices: bool = False,
    ):
        self.fetch_price_panel_calls += 1
        index = pd.date_range("2025-01-01", periods=3, freq="B")
        price_frame = pd.DataFrame({"US.AAPL": [100.0, 101.0, 102.0]}, index=index)
        if include_benchmark_in_prices:
            price_frame["US.VT"] = [50.0, 51.0, 52.0]
        benchmark = pd.Series([50.0, 51.0, 52.0], index=index, name="benchmark")
        return price_frame, benchmark

    def fetch_market_state(self, code_list):
        return pd.DataFrame({"code": list(code_list), "market_state": ["MORNING"] * len(code_list)})

    def fetch_market_snapshot(self, code_list):
        return pd.DataFrame({"code": list(code_list), "last_price": [100.0] * len(code_list)})

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


@dataclass
class FakeTradeClient:
    submit_order_calls: int = 0
    account_value: float = 100_000.0
    buying_power: float | None = None
    position_frame: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame({"code": [], "qty": []})
    )
    order_frame: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame({
            "order_id": [], "order_status": [], "code": [],
            "trd_side": [], "qty": [], "price": [], "filled_quantity": [], "remark": []
        })
    )

    def get_position_frame(self):
        return self.position_frame.copy()

    def get_order_frame(self, refresh_cache: bool = True) -> pd.DataFrame:
        return self.order_frame.copy()

    def get_account_value(self) -> float:
        return self.account_value

    def get_buying_power(self) -> float:
        return self.buying_power if self.buying_power is not None else self.account_value

    def get_matching_active_order(self, instruction, refresh_cache: bool = True):
        return None

    def submit_order(self, instruction):
        self.submit_order_calls += 1
        return pd.DataFrame({"order_id": [self.submit_order_calls], "order_status": ["FILLED"]})

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


@dataclass
class FakeStrategy:
    decide_calls: int = 0

    def decide(self, prices, as_of):
        from moomoo_bot.strategy.base import TradeDecision
        self.decide_calls += 1
        return TradeDecision(
            as_of=as_of,
            target_weights={"US.AAPL": 1.0},
            reason="test",
        )


@dataclass
class FakeStateStore:
    risk_state: PersistentRiskState = field(default_factory=PersistentRiskState)
    previous_equity: EquitySnapshot | None = None
    saved_states: list[PersistentRiskState] = field(default_factory=list)
    recorded_equity: list[dict] = field(default_factory=list)
    recorded_positions: list[dict] = field(default_factory=list)
    recorded_orders: list = field(default_factory=list)
    pending_orders: list = field(default_factory=list)
    order_status_updates: list = field(default_factory=list)
    order_status_update_details: list = field(default_factory=list)
    cleanup_calls: list[int] = field(default_factory=list)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def load_risk_state(self) -> PersistentRiskState:
        return PersistentRiskState(**self.risk_state.__dict__)

    def save_risk_state(self, state: PersistentRiskState) -> None:
        self.saved_states.append(PersistentRiskState(**state.__dict__))
        self.risk_state = state

    def get_latest_equity_before_market_date(self, market_date: str) -> EquitySnapshot | None:
        if self.previous_equity is None:
            return None
        if (
            self.previous_equity.market_date is not None
            and self.previous_equity.market_date < market_date
        ):
            return self.previous_equity
        return None

    def record_equity(self, account_value, cash, positions, market_date=None):
        self.recorded_equity.append({"account_value": account_value, "market_date": market_date})

    def record_positions(self, positions, prices):
        self.recorded_positions.append({"positions": dict(positions)})

    def record_order(self, record) -> int:
        self.recorded_orders.append(record)
        return len(self.recorded_orders)

    def get_pending_orders(self) -> list:
        return list(self.pending_orders)

    def update_order_status(
        self,
        order_id,
        status,
        filled_quantity,
        fill_price=None,
        broker_accepted_price=None,
        fee_amount=None,
        filled_at=None,
    ):
        self.order_status_updates.append((str(order_id), status, filled_quantity))
        self.order_status_update_details.append({
            "fill_price": fill_price,
            "broker_accepted_price": broker_accepted_price,
            "fee_amount": fee_amount,
            "filled_at": filled_at,
        })

    def update_order_id(self, old_order_id, new_order_id):
        pass

    def cleanup_old_equity(self, keep_days: int = 365) -> int:
        self.cleanup_calls.append(keep_days)
        return 0

    def get_equity_at_month_start(self, market_date: str):
        return None

    def get_recent_realizations(self, n: int):
        return []

    def close(self) -> None:
        pass


def _default_settings(**kwargs) -> Settings:
    return Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# run_auto_monitor tests
# ---------------------------------------------------------------------------

def test_run_auto_monitor_exits_immediately_on_kill_switch(monkeypatch):
    from moomoo_bot import orchestrator

    settings = _default_settings()
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()

    monkeypatch.setattr(orchestrator, "_is_kill_switch_active", lambda: True)

    orchestrator.run_auto_monitor(
        settings=settings,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        poll_seconds=1,
        max_consecutive_failures=5,
        quote_client=quote_client,
        trade_client=trade_client,
        state_store=FakeStateStore(),
    )

    assert quote_client.fetch_price_panel_calls == 0
    assert trade_client.submit_order_calls == 0


def test_run_auto_monitor_retries_on_failure_then_stops(monkeypatch):
    """Consecutive failures should stop the monitor after reaching max_consecutive_failures."""
    from moomoo_bot import orchestrator
    import moomoo_bot.orchestrator.cycle as cycle_module

    settings = _default_settings()
    sleep_calls: list[float] = []
    cycle_calls = 0

    def fake_execute_trading_cycle(**kwargs):
        nonlocal cycle_calls
        cycle_calls += 1
        raise RuntimeError("simulated failure")

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(cycle_module, "execute_trading_cycle", fake_execute_trading_cycle)

    orchestrator.run_auto_monitor(
        settings=settings,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        poll_seconds=60,
        max_consecutive_failures=3,
        quote_client=FakeQuoteClient(),
        trade_client=FakeTradeClient(),
        strategy=FakeStrategy(),
        sleep_fn=fake_sleep,
        state_store=FakeStateStore(),
    )

    # Should have called cycle exactly max_consecutive_failures times then stopped
    assert cycle_calls == 3
    # Should have slept with backoff between failures
    assert len(sleep_calls) == 2  # sleep after failure 1 and 2, stop after 3


def test_run_auto_monitor_sleeps_between_cycles():
    """Monitor calls sleep between successful trading cycles."""
    from moomoo_bot import orchestrator

    settings = _default_settings()
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    strategy = FakeStrategy()
    state_store = FakeStateStore()
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        raise KeyboardInterrupt

    try:
        orchestrator.run_auto_monitor(
            settings=settings,
            symbols=["US.AAPL"],
            benchmark_symbol="US.VT",
            history_days=2200,
            capital=None,
            fx_jpy_per_usd=None,
            minimum_order_value=5.0,
            poll_seconds=900,
            max_consecutive_failures=5,
            quote_client=quote_client,
            trade_client=trade_client,
            strategy=strategy,
            sleep_fn=fake_sleep,
            state_store=state_store,
        )
    except KeyboardInterrupt:
        pass

    assert sleep_calls == [900]
    assert strategy.decide_calls == 1


def test_run_auto_monitor_records_equity_after_cycle():
    """Monitor records equity snapshot after each successful cycle."""
    from moomoo_bot import orchestrator

    settings = _default_settings()
    state_store = FakeStateStore()
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        raise KeyboardInterrupt

    try:
        orchestrator.run_auto_monitor(
            settings=settings,
            symbols=["US.AAPL"],
            benchmark_symbol="US.VT",
            history_days=2200,
            capital=None,
            fx_jpy_per_usd=None,
            minimum_order_value=5.0,
            poll_seconds=1,
            max_consecutive_failures=5,
            quote_client=FakeQuoteClient(),
            trade_client=FakeTradeClient(),
            strategy=FakeStrategy(),
            sleep_fn=fake_sleep,
            state_store=state_store,
        )
    except KeyboardInterrupt:
        pass

    assert len(state_store.recorded_equity) >= 1
