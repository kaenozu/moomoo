from __future__ import annotations

import pandas as pd
import pytest

from moomoo_bot.research import (
    _annualized_return,
    _split_period_boundaries,
    default_momentum_search_configs,
    search_momentum_candidates,
    search_satellite_candidates,
)
from moomoo_bot.strategy.momentum import MonthlyMomentumRotationConfig


def test_search_ranks_stronger_momentum_configuration_first() -> None:
    index = pd.date_range("2024-01-01", periods=120, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [100 + i * 3 for i in range(120)],
            "US.MSFT": [100 + i for i in range(120)],
            "US.NVDA": [100 for _ in range(120)],
        },
        index=index,
    )
    benchmark = pd.Series([100 + i * 0.2 for i in range(120)], index=index, name="benchmark")

    results = search_momentum_candidates(
        prices,
        benchmark,
        configs=[
            MonthlyMomentumRotationConfig(lookback_days=20, trend_days=20, top_n=1, skip_days=5, rebalance_days=5),
            MonthlyMomentumRotationConfig(lookback_days=20, trend_days=20, top_n=2, skip_days=5, rebalance_days=5),
        ],
        split_ratio=0.7,
    )

    assert results[0].config.top_n == 1
    assert results[0].test_excess >= results[1].test_excess


def test_annualized_return_normalizes_to_period_start() -> None:
    curve = pd.Series(
        [100.0, 110.0],
        index=pd.to_datetime(["2024-01-01", "2025-01-01"]),
    )

    expected = (110.0 / 100.0) ** (1.0 / (366 / 365.25)) - 1.0

    assert _annualized_return(curve) == pytest.approx(expected)


def test_split_period_boundaries_do_not_overlap() -> None:
    index = pd.date_range("2024-01-01", periods=10, freq="B")

    train_end, test_start = _split_period_boundaries(index, 0.7)

    assert train_end < test_start
    assert train_end == index[6]
    assert test_start == index[7]


def test_search_satellite_candidates_ranks_full_allocation_first() -> None:
    index = pd.date_range("2024-01-01", periods=140, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [100 + i * 3 for i in range(140)],
            "US.MSFT": [100 + i for i in range(140)],
            "US.NVDA": [100 + i * 0.2 for i in range(140)],
        },
        index=index,
    )
    benchmark = pd.Series([100 + i * 0.5 for i in range(140)], index=index, name="benchmark")

    results = search_satellite_candidates(
        prices,
        benchmark,
        configs=[
            MonthlyMomentumRotationConfig(lookback_days=20, trend_days=20, top_n=1, skip_days=5, rebalance_days=5),
        ],
        satellite_weights=[0.0, 0.5, 1.0],
        split_ratio=0.7,
    )

    assert results[0].satellite_weight == 1.0

    core_only = next(result for result in results if result.satellite_weight == 0.0)
    full_allocation = next(result for result in results if result.satellite_weight == 1.0)

    assert core_only.full_excess == pytest.approx(0.0)
    assert full_allocation.full_excess > core_only.full_excess


def test_default_momentum_search_configs_carry_min_hold_days() -> None:
    configs = default_momentum_search_configs(min_hold_days=21)

    assert configs
    assert all(config.min_hold_days == 21 for config in configs)
