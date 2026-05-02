"""Tests for orchestrator/risk_checks.py - risk check orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pytest

from moomoo_bot.config import Settings
from moomoo_bot.risk import RiskState
from moomoo_bot.state import EquitySnapshot, PersistentRiskState


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------

@dataclass
class FakeStateStore:
    risk_state: PersistentRiskState = field(default_factory=PersistentRiskState)
    previous_equity: EquitySnapshot | None = None
    saved_states: list[PersistentRiskState] = field(default_factory=list)
    month_start_equity: EquitySnapshot | None = None
    recent_realizations: list = field(default_factory=list)

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

    def get_equity_at_month_start(self, market_date: str) -> EquitySnapshot | None:
        return self.month_start_equity

    def get_recent_realizations(self, n: int) -> list:
        return self.recent_realizations


@dataclass
class FakeTradeClient:
    submit_order_calls: int = 0
    submitted_sides: list[str] = field(default_factory=list)

    def submit_order(self, instruction):
        self.submit_order_calls += 1
        self.submitted_sides.append(str(instruction.side))
        return pd.DataFrame({"order_id": [self.submit_order_calls], "order_status": ["FILLED"]})

    def get_order_frame(self, refresh_cache: bool = True) -> pd.DataFrame:
        return pd.DataFrame()

    def get_matching_active_order(self, instruction, refresh_cache: bool = True):
        return None


def _make_benchmark_series(last_drop_pct: float = 0.0) -> pd.Series:
    """Create a benchmark series where the last bar drops by last_drop_pct."""
    base = 100.0
    last = base * (1.0 - last_drop_pct)
    return pd.Series([base, base, last])


def _default_settings(**kwargs) -> Settings:
    return Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        **kwargs,
    )


def _make_risk_state(halted: bool = False, halted_reason: str | None = None) -> RiskState:
    state = RiskState()
    state.halted = halted
    state.halted_reason = halted_reason
    return state


def _make_persistent(halted: bool = False, halted_reason: str | None = None) -> PersistentRiskState:
    return PersistentRiskState(halted=halted, halted_reason=halted_reason)


# ---------------------------------------------------------------------------
# check_daily_loss_halt
# ---------------------------------------------------------------------------

def test_check_daily_loss_halt_returns_none_when_not_halted():
    from moomoo_bot.orchestrator.risk_checks import check_daily_loss_halt

    result = check_daily_loss_halt(
        risk_state=_make_risk_state(halted=False),
        persistent_risk_state=_make_persistent(),
        state_store=FakeStateStore(),
        settings=_default_settings(),
        account_value=100_000.0,
        market_date="2025-01-03",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        auto_mode=False,
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result is None


def test_check_daily_loss_halt_returns_none_when_halted_for_other_reason():
    from moomoo_bot.orchestrator.risk_checks import check_daily_loss_halt

    result = check_daily_loss_halt(
        risk_state=_make_risk_state(halted=True, halted_reason="max_drawdown:10%"),
        persistent_risk_state=_make_persistent(),
        state_store=FakeStateStore(),
        settings=_default_settings(),
        account_value=100_000.0,
        market_date="2025-01-03",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        auto_mode=False,
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result is None


def test_check_daily_loss_halt_returns_true_when_already_daily_loss_halted():
    from moomoo_bot.orchestrator.risk_checks import check_daily_loss_halt

    result = check_daily_loss_halt(
        risk_state=_make_risk_state(halted=True, halted_reason="daily_loss_limit:6%"),
        persistent_risk_state=_make_persistent(),
        state_store=FakeStateStore(),
        settings=_default_settings(),
        account_value=100_000.0,
        market_date="2025-01-03",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        auto_mode=False,
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result == (True, False)


# ---------------------------------------------------------------------------
# check_market_shock
# ---------------------------------------------------------------------------

def test_check_market_shock_returns_none_below_threshold():
    from moomoo_bot.orchestrator.risk_checks import check_market_shock

    settings = _default_settings(market_shock_drop_pct=0.10)
    benchmark = _make_benchmark_series(last_drop_pct=0.03)  # 3% drop, below 10%

    result = check_market_shock(
        benchmark_series=benchmark,
        settings=settings,
        risk_state=_make_risk_state(),
        persistent_risk_state=_make_persistent(),
        state_store=FakeStateStore(),
        account_value=100_000.0,
        market_date="2025-01-03",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result is None


def test_check_market_shock_returns_true_above_threshold():
    from moomoo_bot.orchestrator.risk_checks import check_market_shock

    settings = _default_settings(market_shock_drop_pct=0.05)
    benchmark = _make_benchmark_series(last_drop_pct=0.07)  # 7% drop, above 5%

    result = check_market_shock(
        benchmark_series=benchmark,
        settings=settings,
        risk_state=_make_risk_state(),
        persistent_risk_state=_make_persistent(),
        state_store=FakeStateStore(),
        account_value=100_000.0,
        market_date="2025-01-03",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result == (True, False)


def test_check_market_shock_zero_threshold_returns_none():
    from moomoo_bot.orchestrator.risk_checks import check_market_shock

    settings = _default_settings(market_shock_drop_pct=0.0)
    benchmark = _make_benchmark_series(last_drop_pct=0.50)  # 50% drop

    result = check_market_shock(
        benchmark_series=benchmark,
        settings=settings,
        risk_state=_make_risk_state(),
        persistent_risk_state=_make_persistent(),
        state_store=FakeStateStore(),
        account_value=100_000.0,
        market_date="2025-01-03",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result is None


# ---------------------------------------------------------------------------
# check_daily_loss_limit
# ---------------------------------------------------------------------------

def test_check_daily_loss_limit_returns_none_when_no_prior_equity():
    from moomoo_bot.orchestrator.risk_checks import check_daily_loss_limit

    settings = _default_settings(daily_loss_limit_pct=0.05)
    state_store = FakeStateStore(previous_equity=None)

    result = check_daily_loss_limit(
        settings=settings,
        state_store=state_store,
        risk_state=_make_risk_state(),
        persistent_risk_state=_make_persistent(),
        account_value=90_000.0,
        market_date="2025-01-03",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result is None


def test_check_daily_loss_limit_returns_true_when_breached():
    from moomoo_bot.orchestrator.risk_checks import check_daily_loss_limit

    settings = _default_settings(daily_loss_limit_pct=0.05)
    state_store = FakeStateStore(
        previous_equity=EquitySnapshot(
            timestamp="2025-01-02T21:00:00+00:00",
            account_value=100_000.0,
            cash=0.0,
            positions_json="{}",
            market_date="2025-01-02",
        )
    )

    result = check_daily_loss_limit(
        settings=settings,
        state_store=state_store,
        risk_state=_make_risk_state(),
        persistent_risk_state=_make_persistent(),
        account_value=94_000.0,  # 6% loss > 5% limit
        market_date="2025-01-03",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result == (True, False)


def test_check_daily_loss_limit_returns_none_when_below_threshold():
    from moomoo_bot.orchestrator.risk_checks import check_daily_loss_limit

    settings = _default_settings(daily_loss_limit_pct=0.05)
    state_store = FakeStateStore(
        previous_equity=EquitySnapshot(
            timestamp="2025-01-02T21:00:00+00:00",
            account_value=100_000.0,
            cash=0.0,
            positions_json="{}",
            market_date="2025-01-02",
        )
    )

    result = check_daily_loss_limit(
        settings=settings,
        state_store=state_store,
        risk_state=_make_risk_state(),
        persistent_risk_state=_make_persistent(),
        account_value=97_000.0,  # 3% loss < 5% limit
        market_date="2025-01-03",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result is None


# ---------------------------------------------------------------------------
# check_monthly_loss_limit
# ---------------------------------------------------------------------------

def test_check_monthly_loss_limit_returns_none_when_no_month_start():
    from moomoo_bot.orchestrator.risk_checks import check_monthly_loss_limit

    settings = _default_settings(monthly_loss_limit_pct=0.15)
    state_store = FakeStateStore(month_start_equity=None)

    result = check_monthly_loss_limit(
        settings=settings,
        state_store=state_store,
        risk_state=_make_risk_state(),
        persistent_risk_state=_make_persistent(),
        account_value=85_000.0,
        market_date="2025-01-15",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result is None


def test_check_monthly_loss_limit_returns_true_when_breached():
    from moomoo_bot.orchestrator.risk_checks import check_monthly_loss_limit

    settings = _default_settings(monthly_loss_limit_pct=0.15)
    state_store = FakeStateStore(
        month_start_equity=EquitySnapshot(
            timestamp="2025-01-01T00:00:00+00:00",
            account_value=100_000.0,
            cash=0.0,
            positions_json="{}",
            market_date="2025-01-01",
        )
    )

    result = check_monthly_loss_limit(
        settings=settings,
        state_store=state_store,
        risk_state=_make_risk_state(),
        persistent_risk_state=_make_persistent(),
        account_value=83_000.0,  # 17% loss > 15% limit
        market_date="2025-01-15",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result == (True, False)


def test_check_monthly_loss_limit_returns_none_when_below_threshold():
    from moomoo_bot.orchestrator.risk_checks import check_monthly_loss_limit

    settings = _default_settings(monthly_loss_limit_pct=0.15)
    state_store = FakeStateStore(
        month_start_equity=EquitySnapshot(
            timestamp="2025-01-01T00:00:00+00:00",
            account_value=100_000.0,
            cash=0.0,
            positions_json="{}",
            market_date="2025-01-01",
        )
    )

    result = check_monthly_loss_limit(
        settings=settings,
        state_store=state_store,
        risk_state=_make_risk_state(),
        persistent_risk_state=_make_persistent(),
        account_value=90_000.0,  # 10% loss < 15% limit
        market_date="2025-01-15",
        current_positions={},
        latest_prices={},
        market_open=True,
        mode_label="paper",
        submit_orders=False,
        trade_client=FakeTradeClient(),
    )
    assert result is None
