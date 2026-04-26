"""Extended tests for risk module."""

import pandas as pd
from moomoo_bot.risk import (
    RiskState,
    update_drawdown_state,
    detect_daily_loss_limit,
    calculate_volatility_scalar,
)


def test_daily_loss_limit_detection():
    """Test daily loss limit detection."""
    # No breach
    reason = detect_daily_loss_limit(100000.0, 99000.0, 0.05)  # 1% loss, 5% limit
    assert reason is None

    # Breach
    reason = detect_daily_loss_limit(100000.0, 94000.0, 0.05)  # 6% loss, 5% limit
    assert reason is not None
    assert "daily_loss_limit" in reason
    assert "6.00%" in reason


def test_drawdown_tier_progression():
    """Test that drawdown tiers progress correctly."""
    state = RiskState(peak_account_value=100000.0)
    max_dd = 0.15
    reset_pct = 0.05

    # Tier 0 -> Tier 1 (at 10% drawdown, which is 2/3 of 15%)
    reason = update_drawdown_state(90000.0, state, max_dd, reset_pct)
    assert reason is None  # Should not halt, just change tier
    assert state.drawdown_tier == 1
    assert state.halted is False

    # Tier 1 -> Tier 2 (halt at 15% drawdown)
    reason = update_drawdown_state(85000.0, state, max_dd, reset_pct)
    assert reason is not None
    assert state.drawdown_tier == 2
    assert state.halted is True
    assert "max_drawdown" in reason


def test_drawdown_recovery():
    """Test drawdown recovery logic."""
    state = RiskState(peak_account_value=100000.0, halted=True, drawdown_tier=2)
    max_dd = 0.15
    reset_pct = 0.05

    # Recover to within reset threshold (within 5% of peak)
    reason = update_drawdown_state(96000.0, state, max_dd, reset_pct)
    assert reason is None
    assert state.halted is False
    assert state.drawdown_tier == 0


def test_volatility_scalar_calculation():
    """Test volatility scalar calculation."""
    # Create a price series
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    prices = pd.Series([100.0 + i * 0.1 for i in range(30)], index=dates)

    # Low volatility should give scalar > 0
    scalar = calculate_volatility_scalar(prices, 21, 0.15)
    assert 0.0 <= scalar <= 1.0

    # High volatility (random)
    prices_high = pd.Series([100.0 + (i % 2) * 10.0 for i in range(30)], index=dates)
    scalar = calculate_volatility_scalar(prices_high, 21, 0.15)
    assert 0.0 <= scalar <= 1.0


def test_new_peak_resets_state():
    """Test that a new peak resets risk state."""
    state = RiskState(peak_account_value=100000.0, halted=True, drawdown_tier=2)

    reason = update_drawdown_state(110000.0, state, 0.15, 0.05)
    assert reason is None
    assert state.peak_account_value == 110000.0
    assert state.halted is False
    assert state.drawdown_tier == 0