"""Tests for the recommendation strategy."""

from __future__ import annotations

import pandas as pd
import pytest

from moomoo_bot.strategy.recommendation import (
    RecommendationConfig,
    RecommendationStrategy,
)
from moomoo_bot.strategy.base import TradeDecision


def test_recommendation_strategy_initialization() -> None:
    """Test that the recommendation strategy initializes correctly."""
    config = RecommendationConfig(
        symbols=["US.AAPL", "US.MSFT"],
        top_n=2,
        weighting_method="equal"
    )
    strategy = RecommendationStrategy(config)
    
    assert strategy.config.symbols == ["US.AAPL", "US.MSFT"]
    assert strategy.config.top_n == 2
    assert strategy.config.weighting_method == "equal"


def test_recommendation_strategy_waits_for_history() -> None:
    """Test that strategy returns empty weights when insufficient history."""
    strategy = RecommendationStrategy(
        RecommendationConfig(
            symbols=["US.AAPL"],
            top_n=1,
            ma_short=20,
            ma_long=50
        )
    )
    # Only 5 days of data, less than ma_long (50)
    prices = pd.DataFrame({"US.AAPL": [100, 101, 102, 103, 104]})
    decision = strategy.decide(prices, prices.index[-1])
    
    assert decision.target_weights == {}
    # Should indicate insufficient data for calculations


def test_recommendation_strategy_equal_weighting() -> None:
    """Test equal weighting of recommendations."""
    strategy = RecommendationStrategy(
        RecommendationConfig(
            symbols=["US.AAPL", "US.MSFT", "US.GOOGL"],
            top_n=2,
            weighting_method="equal",
            ma_short=5,  # Short for testing
            ma_long=10
        )
    )
    
    # Create price data where AAPL and MSFT have good scores
    index = pd.date_range("2025-01-01", periods=15, freq="B")
    prices = pd.DataFrame({
        "US.AAPL": [100 + i * 2 for i in range(15)],  # Strong uptrend
        "US.MSFT": [100 + i * 1.5 for i in range(15)],  # Moderate uptrend
        "US.GOOGL": [100 - i * 0.5 for i in range(15)],  # Downtrend
    }, index=index)
    
    decision = strategy.decide(prices, index[-1])
    
    # Should select top 2 symbols (AAPL and MSFT) with equal weights
    assert set(decision.target_weights.keys()) == {"US.AAPL", "US.MSFT"}
    assert decision.target_weights["US.AAPL"] == pytest.approx(0.5, rel=1e-9)
    assert decision.target_weights["US.MSFT"] == pytest.approx(0.5, rel=1e-9)
    assert decision.reason.startswith("recommendations:")


def test_recommendation_strategy_score_weighting() -> None:
    """Test score-based weighting of recommendations."""
    strategy = RecommendationStrategy(
        RecommendationConfig(
            symbols=["US.AAPL", "US.MSFT"],
            top_n=2,
            weighting_method="score",
            ma_short=5,
            ma_long=10
        )
    )
    
    # Create price data where AAPL has much better momentum than MSFT
    index = pd.date_range("2025-01-01", periods=15, freq="B")
    prices = pd.DataFrame({
        "US.AAPL": [100 + i * 3 for i in range(15)],  # Strong uptrend
        "US.MSFT": [100 + i * 0.5 for i in range(15)],  # Weak uptrend
    }, index=index)
    
    decision = strategy.decide(prices, index[-1])
    
    # Should select both symbols but weight AAPL higher due to better score
    assert set(decision.target_weights.keys()) == {"US.AAPL", "US.MSFT"}
    assert decision.target_weights["US.AAPL"] > decision.target_weights["US.MSFT"]
    assert decision.target_weights["US.AAPL"] + decision.target_weights["US.MSFT"] == pytest.approx(1.0, rel=1e-9)
    assert decision.reason.startswith("recommendations:")


def test_recommendation_strategy_top_n_limit() -> None:
    """Test that strategy respects top_n limit."""
    strategy = RecommendationStrategy(
        RecommendationConfig(
            symbols=["US.AAPL", "US.MSFT", "US.GOOGL", "US.TSLA"],
            top_n=2,
            weighting_method="equal",
            ma_short=5,
            ma_long=10
        )
    )
    
    # All symbols showing uptrend
    index = pd.date_range("2025-01-01", periods=15, freq="B")
    prices = pd.DataFrame({
        "US.AAPL": [100 + i for i in range(15)],
        "US.MSFT": [100 + i for i in range(15)],
        "US.GOOGL": [100 + i for i in range(15)],
        "US.TSLA": [100 + i for i in range(15)],
    }, index=index)
    
    decision = strategy.decide(prices, index[-1])
    
    # Should only select top 2 symbols
    assert len(decision.target_weights) == 2
    assert sum(decision.target_weights.values()) == pytest.approx(1.0, rel=1e-9)


def test_recommendation_strategy_context_manager() -> None:
    """Test that the strategy works as a context manager."""
    with RecommendationStrategy() as strategy:
        assert isinstance(strategy, RecommendationStrategy)
        # Should be able to call decide without error
        prices = pd.DataFrame({"US.AAPL": [100, 101, 102]})
        decision = strategy.decide(prices, pd.Timestamp("2025-01-03"))
        # Should return a TradeDecision (even if empty)
        assert isinstance(decision, TradeDecision)


def test_recommendation_strategy_close() -> None:
    """Test that closing the strategy works correctly."""
    class FakeQuoteClient:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_client = FakeQuoteClient()
    strategy = RecommendationStrategy(quote_client_factory=lambda: fake_client)
    assert strategy._quote_client is None  # Not initialized yet

    # Initialize client
    client = strategy._get_quote_client()
    assert client is fake_client
    assert strategy._quote_client is fake_client

    # Close should clean up
    strategy.close()
    assert fake_client.closed is True
    assert strategy._quote_client is None


def test_recommendation_strategy_empty_symbols() -> None:
    """Test behavior with empty symbol list."""
    strategy = RecommendationStrategy(
        RecommendationConfig(
            symbols=[],  # Empty symbols
            top_n=5
        )
    )
    
    prices = pd.DataFrame({"US.AAPL": [100, 101, 102]})
    decision = strategy.decide(prices, prices.index[-1])
    
    assert decision.target_weights == {}
    assert decision.reason == "no_scores_calculated"
