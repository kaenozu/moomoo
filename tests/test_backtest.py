from __future__ import annotations

from moomoo_bot.backtest import make_demo_prices, run_backtest
from moomoo_bot.backtest.engine import blend_result_with_benchmark
from moomoo_bot.strategy.momentum import MomentumRotationConfig, MomentumRotationStrategy


def test_backtest_returns_metrics() -> None:
    prices, benchmark = make_demo_prices(["US.AAPL", "US.MSFT", "US.NVDA"], periods=260, seed=11)
    strategy = MomentumRotationStrategy(MomentumRotationConfig(lookback_days=20, trend_days=40, top_n=2))
    result = run_backtest(prices, benchmark, strategy)

    assert len(result.equity_curve) == len(prices)
    assert len(result.benchmark_curve) == len(prices)
    assert isinstance(result.total_return, float)
    assert isinstance(result.outperformance, float)


def test_blended_backtest_can_improve_relative_return() -> None:
    prices, benchmark = make_demo_prices(["US.AAPL", "US.MSFT", "US.NVDA", "US.AMZN"], periods=260, seed=11)
    strategy = MomentumRotationStrategy(MomentumRotationConfig(lookback_days=20, trend_days=40, top_n=2))
    result = run_backtest(prices, benchmark, strategy)
    blended = blend_result_with_benchmark(result, 0.05)

    assert blended.outperformance > result.outperformance
