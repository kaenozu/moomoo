from __future__ import annotations

import pandas as pd

from moomoo_bot.strategy.momentum import (
    MomentumRotationConfig,
    MomentumRotationStrategy,
    MonthlyMomentumRotationConfig,
    MonthlyMomentumRotationStrategy,
)


def test_strategy_waits_for_history() -> None:
    strategy = MomentumRotationStrategy(MomentumRotationConfig(lookback_days=5, trend_days=10, top_n=2))
    prices = pd.DataFrame({"US.AAPL": [100, 101, 102, 103, 104, 105]})
    decision = strategy.decide(prices, prices.index[-1])
    assert decision.target_weights == {}
    assert decision.reason == "insufficient_history"


def test_strategy_selects_top_symbols_above_trend() -> None:
    strategy = MomentumRotationStrategy(MomentumRotationConfig(lookback_days=5, trend_days=10, top_n=2))
    index = pd.date_range("2025-01-01", periods=12, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 120, 125],
            "US.MSFT": [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89],
            "US.NVDA": [100, 100, 101, 102, 103, 104, 105, 106, 107, 108, 112, 118],
        },
        index=index,
    )
    decision = strategy.decide(prices, index[-1])
    assert decision.target_weights == {"US.AAPL": 0.5, "US.NVDA": 0.5}
    assert decision.reason.startswith("top_momentum:")


def test_monthly_momentum_strategy_rebalances_and_holds() -> None:
    strategy = MonthlyMomentumRotationStrategy(
        MonthlyMomentumRotationConfig(lookback_days=20, trend_days=20, top_n=1, skip_days=5, rebalance_days=5)
    )
    index = pd.date_range("2025-01-01", periods=40, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [100 + i * 2 for i in range(40)],
            "US.MSFT": [100 + i for i in range(40)],
            "US.NVDA": [100 for _ in range(40)],
        },
        index=index,
    )

    first_non_empty = None
    next_day_weights = None
    for position, current_date in enumerate(index):
        decision = strategy.decide(prices.iloc[: position + 1], current_date)
        if decision.target_weights and first_non_empty is None:
            first_non_empty = decision.target_weights
            continue
        if first_non_empty is not None and next_day_weights is None:
            next_day_weights = decision.target_weights
            break

    assert first_non_empty == {"US.AAPL": 1.0}
    assert next_day_weights == first_non_empty


def test_monthly_momentum_strategy_stays_in_cash_without_trend() -> None:
    strategy = MonthlyMomentumRotationStrategy(
        MonthlyMomentumRotationConfig(lookback_days=20, trend_days=20, top_n=1, skip_days=5, rebalance_days=5)
    )
    index = pd.date_range("2025-01-01", periods=40, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [120 - i for i in range(40)],
            "US.MSFT": [110 - i for i in range(40)],
            "US.NVDA": [100 - i for i in range(40)],
        },
        index=index,
    )

    decision = strategy.decide(prices, index[-1])
    assert decision.target_weights == {}
    assert decision.reason == "no_symbols_above_trend"


def test_monthly_momentum_strategy_respects_min_hold_days() -> None:
    strategy = MonthlyMomentumRotationStrategy(
        MonthlyMomentumRotationConfig(
            lookback_days=1,
            trend_days=2,
            top_n=1,
            skip_days=0,
            rebalance_days=1,
            min_hold_days=3,
        )
    )
    index = pd.date_range("2025-01-01", periods=5, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [100.0, 103.0, 107.0, 110.0, 113.0],
            "US.MSFT": [90.0, 91.0, 92.0, 93.0, 120.0],
        },
        index=index,
    )

    strategy.decide(prices.iloc[:1], index[0])
    first_decision = strategy.decide(prices.iloc[:3], index[2])
    assert first_decision.target_weights == {"US.AAPL": 1.0}

    second_decision = strategy.decide(prices.iloc[:4], index[3])
    assert second_decision.target_weights == {"US.AAPL": 1.0}

    third_decision = strategy.decide(prices.iloc[:5], index[4])
    assert third_decision.target_weights == {"US.AAPL": 1.0}
