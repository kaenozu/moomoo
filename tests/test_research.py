from __future__ import annotations

import pandas as pd
import pytest

from moomoo_bot.backtest.engine import annualized_return as _annualized_return
from moomoo_bot.regime import (
    _derive_market_regime_segments,
    _rolling_walk_forward_boundaries,
    _split_period_boundaries,
)
from moomoo_bot.research import (
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
    benchmark = pd.Series(
        [100 + i * 0.2 for i in range(120)], index=index, name="benchmark"
    )

    results = search_momentum_candidates(
        prices,
        benchmark,
        configs=[
            MonthlyMomentumRotationConfig(
                lookback_days=20, trend_days=20, top_n=1, skip_days=5, rebalance_days=5
            ),
            MonthlyMomentumRotationConfig(
                lookback_days=20, trend_days=20, top_n=2, skip_days=5, rebalance_days=5
            ),
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


def test_rolling_walk_forward_boundaries_yield_multiple_windows() -> None:
    index = pd.date_range("2024-01-01", periods=120, freq="B")

    windows = _rolling_walk_forward_boundaries(
        index,
        train_size=40,
        test_size=20,
        step_size=20,
    )

    assert len(windows) == 4
    assert windows[0] == (index[39], index[40], index[59])
    assert windows[-1] == (index[99], index[100], index[119])


def test_market_regime_segmentation_detects_crash_and_recovery() -> None:
    benchmark = pd.Series(
        [
            *[100 + i * 0.8 for i in range(30)],
            *[124 - i * 2.0 for i in range(25)],
            *[74 + i * 1.8 for i in range(25)],
            *[119 + ((-1) ** i) * 0.2 for i in range(30)],
        ],
        index=pd.date_range("2024-01-01", periods=110, freq="B"),
        name="benchmark",
    )

    regimes = _derive_market_regime_segments(
        benchmark,
        lookback_days=10,
        min_segment_days=8,
    )

    labels = {segment.label for segment in regimes}

    assert "crash" in labels
    assert "recovery" in labels


def test_search_momentum_candidates_include_walk_forward_and_regime_metrics() -> None:
    index = pd.date_range("2024-01-01", periods=180, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [100 + i * 2.5 for i in range(180)],
            "US.MSFT": [100 + i * 0.8 for i in range(180)],
            "US.NVDA": [120 - i * 0.3 for i in range(180)],
        },
        index=index,
    )
    benchmark = pd.Series(
        [
            *[100 + i * 0.8 for i in range(50)],
            *[140 - i * 1.6 for i in range(40)],
            *[76 + i * 1.5 for i in range(40)],
            *[136 + i * 0.2 for i in range(50)],
        ],
        index=index,
        name="benchmark",
    )

    results = search_momentum_candidates(
        prices,
        benchmark,
        configs=[
            MonthlyMomentumRotationConfig(
                lookback_days=20,
                trend_days=20,
                top_n=1,
                skip_days=5,
                rebalance_days=5,
            ),
            MonthlyMomentumRotationConfig(
                lookback_days=20,
                trend_days=20,
                top_n=2,
                skip_days=5,
                rebalance_days=5,
            ),
        ],
        split_ratio=0.7,
        walk_forward_train_size=60,
        walk_forward_test_size=20,
        walk_forward_step_size=20,
        regime_lookback_days=10,
        regime_min_segment_days=8,
    )

    assert results[0].walk_forward_window_count == 6
    assert results[0].walk_forward_mean_excess >= results[1].walk_forward_mean_excess
    assert results[0].regime_scores
    assert results[0].regime_worst_excess <= results[0].walk_forward_mean_excess


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
    benchmark = pd.Series(
        [100 + i * 0.5 for i in range(140)], index=index, name="benchmark"
    )

    results = search_satellite_candidates(
        prices,
        benchmark,
        configs=[
            MonthlyMomentumRotationConfig(
                lookback_days=20, trend_days=20, top_n=1, skip_days=5, rebalance_days=5
            ),
        ],
        satellite_weights=[0.0, 0.5, 1.0],
        split_ratio=0.7,
    )

    assert results[0].satellite_weight == 1.0

    core_only = next(result for result in results if result.satellite_weight == 0.0)
    full_allocation = next(
        result for result in results if result.satellite_weight == 1.0
    )

    assert core_only.full_excess == pytest.approx(0.0)
    assert full_allocation.full_excess > core_only.full_excess


def test_default_momentum_search_configs_carry_min_hold_days() -> None:
    configs = default_momentum_search_configs(min_hold_days=21)

    assert configs
    assert all(config.min_hold_days == 21 for config in configs)


def test_search_momentum_candidates_apply_transaction_cost_profile() -> None:
    index = pd.date_range("2024-01-01", periods=90, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [100.0 + (i * 1.5) for i in range(90)],
            "US.MSFT": [100.0 + (i * 0.3) for i in range(90)],
        },
        index=index,
    )
    benchmark = pd.Series(
        [100.0 + (i * 0.4) for i in range(90)],
        index=index,
        name="benchmark",
    )
    configs = [
        MonthlyMomentumRotationConfig(
            lookback_days=20,
            trend_days=20,
            top_n=1,
            skip_days=5,
            rebalance_days=5,
        )
    ]

    no_cost_results = search_momentum_candidates(
        prices,
        benchmark,
        configs=configs,
        split_ratio=0.7,
        transaction_cost_per_trade=0.0,
        transaction_cost_bps=0.0,
    )
    with_cost_results = search_momentum_candidates(
        prices,
        benchmark,
        configs=configs,
        split_ratio=0.7,
        transaction_cost_per_trade=0.0,
        transaction_cost_bps=50.0,
    )

    assert with_cost_results[0].full_result.transaction_costs > 0.0
    assert (
        with_cost_results[0].full_result.total_return
        < no_cost_results[0].full_result.total_return
    )
