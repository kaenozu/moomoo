"""Market regime classification tests."""

from __future__ import annotations

import pandas as pd
import pytest

from moomoo_bot.regime import (
    MarketRegimeSegment,
    RegimePerformance,
    _summarize_period,
    _split_period_boundaries,
    _rolling_walk_forward_boundaries,
)


class TestMarketRegimeSegment:
    """Tests for MarketRegimeSegment dataclass."""
    
    def test_segment_creation(self) -> None:
        start = pd.Timestamp("2020-01-01")
        end = pd.Timestamp("2020-12-31")
        segment = MarketRegimeSegment(
            label="bull",
            start_date=start,
            end_date=end,
            observation_count=252,
        )
        
        assert segment.label == "bull"
        assert segment.start_date == start
        assert segment.end_date == end
        assert segment.observation_count == 252

    def test_segment_is_frozen(self) -> None:
        """Test that MarketRegimeSegment is immutable."""
        segment = MarketRegimeSegment(
            label="bull",
            start_date=pd.Timestamp("2020-01-01"),
            end_date=pd.Timestamp("2020-12-31"),
            observation_count=252,
        )
        
        with pytest.raises(AttributeError):
            segment.label = "bear"


class TestRegimePerformance:
    """Tests for RegimePerformance dataclass."""
    
    def test_performance_creation(self) -> None:
        start = pd.Timestamp("2020-01-01")
        end = pd.Timestamp("2020-12-31")
        perf = RegimePerformance(
            label="bull",
            start_date=start,
            end_date=end,
            observation_count=252,
            excess_return=0.15,
            strategy_cagr=0.20,
            benchmark_cagr=0.10,
            max_drawdown=-0.15,
        )
        
        assert perf.label == "bull"
        assert perf.excess_return == 0.15
        assert perf.strategy_cagr == 0.20
        assert perf.benchmark_cagr == 0.10
        assert perf.max_drawdown == -0.15

    def test_performance_is_frozen(self) -> None:
        """Test that RegimePerformance is immutable."""
        perf = RegimePerformance(
            label="bull",
            start_date=pd.Timestamp("2020-01-01"),
            end_date=pd.Timestamp("2020-12-31"),
            observation_count=252,
            excess_return=0.15,
            strategy_cagr=0.20,
            benchmark_cagr=0.10,
            max_drawdown=-0.15,
        )
        
        with pytest.raises(AttributeError):
            perf.label = "bear"


class TestSummarizePeriod:
    """Tests for _summarize_period function."""
    
    def test_summarize_period_basic(self) -> None:
        """Test basic period summarization."""
        dates = pd.date_range("2020-01-01", periods=252)
        equity = pd.Series([100 * (1.001 ** i) for i in range(252)], index=dates)
        benchmark = pd.Series([100 * (1.0005 ** i) for i in range(252)], index=dates)
        
        result = _summarize_period(
            equity,
            benchmark,
            dates[0],
            dates[-1],
        )
        
        assert "total_return" in result
        assert "benchmark_return" in result
        assert "cagr" in result
        assert "benchmark_cagr" in result
        assert "sharpe" in result
        assert "max_drawdown" in result
        
        # Strategy should outperform benchmark
        assert result["cagr"] > result["benchmark_cagr"]

    def test_summarize_period_partial(self) -> None:
        """Test summarization of partial period."""
        dates = pd.date_range("2020-01-01", periods=252)
        equity = pd.Series(range(100, 100 + 252), index=dates)
        benchmark = pd.Series(range(100, 100 + 252), index=dates)
        
        # Use first half
        result = _summarize_period(
            equity,
            benchmark,
            dates[0],
            dates[125],
        )
        
        assert isinstance(result, dict)
        assert "total_return" in result
        assert "benchmark_return" in result

    def test_summarize_period_insufficient_data(self) -> None:
        """Test that insufficient data raises error."""
        dates = pd.date_range("2020-01-01", periods=2)
        equity = pd.Series([100, 101], index=dates)
        benchmark = pd.Series([100, 101], index=dates)
        
        # Only 1 data point in period
        with pytest.raises(ValueError, match="not enough data"):
            _summarize_period(
                equity,
                benchmark,
                dates[0],
                dates[0],
            )


class TestSplitPeriodBoundaries:
    """Tests for _split_period_boundaries function."""
    
    def test_split_at_50_percent(self) -> None:
        """Test 50/50 split."""
        dates = pd.date_range("2020-01-01", periods=100)
        
        train_end, test_start = _split_period_boundaries(dates, 0.5)
        
        assert train_end < test_start
        train_idx = list(dates).index(train_end)
        test_idx = list(dates).index(test_start)
        assert test_idx == train_idx + 1

    def test_split_at_70_percent(self) -> None:
        """Test 70/30 split."""
        dates = pd.date_range("2020-01-01", periods=100)
        
        train_end, test_start = _split_period_boundaries(dates, 0.7)
        
        train_idx = list(dates).index(train_end)
        test_idx = list(dates).index(test_start)
        assert test_idx == train_idx + 1
        # Should be roughly at 70th position
        assert 65 < train_idx < 75

    def test_split_too_small_ratio(self) -> None:
        """Test that extreme split ratios raise error."""
        dates = pd.date_range("2020-01-01", periods=10)
        
        with pytest.raises(ValueError, match="split_ratio leaves no room"):
            _split_period_boundaries(dates, 0.01)

    def test_split_too_large_ratio(self) -> None:
        """Test that extreme split ratios raise error."""
        dates = pd.date_range("2020-01-01", periods=10)
        
        with pytest.raises(ValueError, match="split_ratio leaves no room"):
            _split_period_boundaries(dates, 0.99)


class TestRollingWalkForwardBoundaries:
    """Tests for _rolling_walk_forward_boundaries function."""
    
    def test_rolling_walk_forward_basic(self) -> None:
        """Test basic rolling walk-forward boundaries."""
        dates = pd.date_range("2020-01-01", periods=100)
        
        windows = _rolling_walk_forward_boundaries(
            dates,
            train_size=60,
            test_size=10,
            step_size=10,
        )
        
        assert len(windows) > 0
        for train_end, test_start, test_end in windows:
            assert train_end < test_start < test_end

    def test_rolling_walk_forward_single_window(self) -> None:
        """Test when only one window fits."""
        dates = pd.date_range("2020-01-01", periods=100)
        
        windows = _rolling_walk_forward_boundaries(
            dates,
            train_size=80,
            test_size=10,
            step_size=5,
        )
        
        # Depending on the exact boundaries, might have 1-2 windows
        assert len(windows) >= 1

    def test_rolling_walk_forward_no_windows(self) -> None:
        """Test when no windows fit."""
        dates = pd.date_range("2020-01-01", periods=50)
        
        windows = _rolling_walk_forward_boundaries(
            dates,
            train_size=40,
            test_size=20,
            step_size=5,
        )
        
        assert len(windows) == 0

    def test_rolling_walk_forward_invalid_train_size(self) -> None:
        """Test that invalid train_size raises error."""
        dates = pd.date_range("2020-01-01", periods=100)
        
        with pytest.raises(ValueError, match="train_size must be greater than 1"):
            _rolling_walk_forward_boundaries(
                dates,
                train_size=1,
                test_size=10,
                step_size=5,
            )

    def test_rolling_walk_forward_invalid_test_size(self) -> None:
        """Test that invalid test_size raises error."""
        dates = pd.date_range("2020-01-01", periods=100)
        
        with pytest.raises(ValueError, match="test_size must be greater than 1"):
            _rolling_walk_forward_boundaries(
                dates,
                train_size=60,
                test_size=0,
                step_size=5,
            )

    def test_rolling_walk_forward_invalid_step_size(self) -> None:
        """Test that invalid step_size raises error."""
        dates = pd.date_range("2020-01-01", periods=100)
        
        with pytest.raises(ValueError, match="step_size must be positive"):
            _rolling_walk_forward_boundaries(
                dates,
                train_size=60,
                test_size=10,
                step_size=-1,
            )

    def test_rolling_walk_forward_step_size_one(self) -> None:
        """Test with step_size=1 (maximum overlap)."""
        dates = pd.date_range("2020-01-01", periods=100)
        
        windows = _rolling_walk_forward_boundaries(
            dates,
            train_size=60,
            test_size=10,
            step_size=1,
        )
        
        # Should have many windows with maximum overlap
        assert len(windows) > 10
