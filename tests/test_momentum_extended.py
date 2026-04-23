"""Extended tests for momentum module."""

import pytest
import pandas as pd
from moomoo_bot.strategy.momentum import (
    MonthlyMomentumRotationStrategy,
    MonthlyMomentumRotationConfig,
)


@pytest.fixture
def sample_prices():
    """Create sample price data for testing."""
    dates = pd.date_range("2024-01-01", periods=300, freq="D")
    symbols = ["US.AAPL", "US.MSFT", "US.GOOG", "US.TSLA", "US.NVDA"]

    # Create price data with momentum
    data = {}
    for i, sym in enumerate(symbols):
        # Give each symbol a different trend
        base = 100.0 + i * 10.0
        trend = 0.001 * (i - 2)  # Some positive, some negative
        noise = 0.02
        prices = [base]
        for j in range(1, len(dates)):
            change = trend + noise * (j % 3 - 1)
            prices.append(prices[-1] * (1 + change))
        data[sym] = prices

    df = pd.DataFrame(data, index=dates)
    # Create benchmark
    df["US.VT"] = df.mean(axis=1)
    return df


def test_strategy_without_fallback(sample_prices):
    """Test strategy without fallback asset."""
    config = MonthlyMomentumRotationConfig(
        lookback_days=63,
        trend_days=50,
        top_n=2,
        skip_days=10,
        rebalance_days=21,
        min_hold_days=0,
        inverse_volatility=False,
        fallback_asset_symbol=None,
        fallback_allocation=0.0,
    )
    strategy = MonthlyMomentumRotationStrategy(config)

    decision = strategy.decide(sample_prices, sample_prices.index[-1])
    assert decision.target_weights is not None
    assert decision.reason is not None


def test_strategy_with_fallback(sample_prices):
    """Test strategy with fallback asset."""
    config = MonthlyMomentumRotationConfig(
        lookback_days=63,
        trend_days=50,
        top_n=2,
        skip_days=10,
        rebalance_days=21,
        min_hold_days=0,
        inverse_volatility=False,
        fallback_asset_symbol="US.VT",
        fallback_allocation=0.5,
    )
    strategy = MonthlyMomentumRotationStrategy(config)

    decision = strategy.decide(sample_prices, sample_prices.index[-1])
    # Should have some allocation
    assert decision.target_weights is not None


def test_inverse_volatility_weighting(sample_prices):
    """Test inverse volatility weighting."""
    config = MonthlyMomentumRotationConfig(
        lookback_days=63,
        trend_days=50,
        top_n=2,
        skip_days=10,
        rebalance_days=21,
        min_hold_days=0,
        inverse_volatility=True,
        fallback_asset_symbol=None,
        fallback_allocation=0.0,
    )
    strategy = MonthlyMomentumRotationStrategy(config)

    decision = strategy.decide(sample_prices, sample_prices.index[-1])
    assert decision.target_weights is not None


def test_min_hold_days(sample_prices):
    """Test that min_hold_days preserves positions."""
    config = MonthlyMomentumRotationConfig(
        lookback_days=63,
        trend_days=50,
        top_n=2,
        skip_days=10,
        rebalance_days=21,
        min_hold_days=42,  # Force holding
        inverse_volatility=False,
        fallback_asset_symbol=None,
        fallback_allocation=0.0,
    )
    strategy = MonthlyMomentumRotationStrategy(config)

    # Make first decision
    decision1 = strategy.decide(sample_prices, sample_prices.index[-1])

    # Make second decision - should preserve first decision if min_hold_days not met
    decision2 = strategy.decide(sample_prices, sample_prices.index[-1])

    # If first decision had symbols, second should preserve them
    if decision1.target_weights:
        assert len(decision2.target_weights) > 0


def test_strategy_reset(sample_prices):
    """Test that reset clears state."""
    config = MonthlyMomentumRotationConfig(
        lookback_days=63,
        trend_days=50,
        top_n=2,
        skip_days=10,
        rebalance_days=21,
        min_hold_days=0,
        inverse_volatility=False,
        fallback_asset_symbol=None,
        fallback_allocation=0.0,
    )
    strategy = MonthlyMomentumRotationStrategy(config)

    # Make a decision
    strategy.decide(sample_prices, sample_prices.index[-1])

    # Reset
    strategy.reset()

    # Should start fresh
    assert strategy._current_weights == {}
    assert strategy._entry_index == {}
    assert strategy._last_rebalance_length == -1