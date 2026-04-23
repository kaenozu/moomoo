from __future__ import annotations

import pandas as pd

from moomoo_bot.backtest import make_demo_prices, run_backtest
from moomoo_bot.backtest.engine import blend_result_with_benchmark
from moomoo_bot.strategy.base import TradeDecision
from moomoo_bot.strategy.momentum import MomentumRotationConfig, MomentumRotationStrategy


def test_backtest_returns_metrics() -> None:
    prices, benchmark = make_demo_prices(["US.AAPL", "US.MSFT", "US.NVDA"], periods=260, seed=11)
    strategy = MomentumRotationStrategy(MomentumRotationConfig(lookback_days=20, trend_days=40, top_n=2))
    result = run_backtest(prices, benchmark, strategy)

    assert len(result.equity_curve) == len(prices)
    assert len(result.benchmark_curve) == len(prices)
    assert isinstance(result.total_return, float)
    assert isinstance(result.outperformance, float)


def test_run_backtest_resets_stateful_strategy_before_use() -> None:
    class StatefulStrategy:
        def __init__(self) -> None:
            self.reset_calls = 0
            self.active = True

        def reset(self) -> None:
            self.reset_calls += 1
            self.active = False

        def decide(self, prices: pd.DataFrame, as_of: pd.Timestamp) -> TradeDecision:
            if self.active:
                return TradeDecision(
                    as_of=as_of,
                    target_weights={"US.AAPL": 1.0},
                    reason="active",
                )
            return TradeDecision(as_of=as_of, target_weights={}, reason="reset")

    prices = pd.DataFrame(
        {"US.AAPL": [100.0, 101.0, 102.0]},
        index=pd.date_range("2025-01-01", periods=3, freq="B"),
    )
    benchmark = pd.Series([100.0, 100.0, 100.0], index=prices.index)

    strategy = StatefulStrategy()
    result = run_backtest(prices, benchmark, strategy)

    assert strategy.reset_calls == 1
    assert result.trade_count == 0
    assert result.total_return == 0.0


def test_blended_backtest_can_improve_relative_return() -> None:
    prices, benchmark = make_demo_prices(["US.AAPL", "US.MSFT", "US.NVDA", "US.AMZN"], periods=260, seed=11)
    strategy = MomentumRotationStrategy(MomentumRotationConfig(lookback_days=20, trend_days=40, top_n=2))
    result = run_backtest(prices, benchmark, strategy)
    blended = blend_result_with_benchmark(result, 0.05)

    assert blended.outperformance > result.outperformance
