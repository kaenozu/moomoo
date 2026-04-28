"""Strategy research and candidate search module.

Purpose: Search and rank momentum strategy candidates and satellite blends.
Related: backtest.py, strategy/momentum.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import pandas as pd

from moomoo_bot.backtest import BacktestResult, run_backtest
from moomoo_bot.backtest.engine import (
    blend_result_with_benchmark as _blend_with_benchmark,
)
from moomoo_bot.regime import (
    MarketRegimeSegment,
    RegimePerformance,
    _derive_market_regime_segments,
    _resolved_walk_forward_step_size,
    _resolved_walk_forward_test_size,
    _resolved_walk_forward_train_size,
    _rolling_walk_forward_boundaries,
    _split_period_boundaries,
    _summarize_period,
    _summarize_regime_performance,
    _summarize_walk_forward_windows,
    _worst_regime_excess,
)
from moomoo_bot.strategy.momentum import (
    MonthlyMomentumRotationConfig,
    MonthlyMomentumRotationStrategy,
)


@dataclass(frozen=True)
class _SearchResultBase:
    config: MonthlyMomentumRotationConfig
    full_result: BacktestResult
    train_excess: float
    test_excess: float
    train_cagr: float
    test_cagr: float
    test_sharpe: float
    train_drawdown: float
    test_drawdown: float
    walk_forward_mean_excess: float = 0.0
    walk_forward_worst_excess: float = 0.0
    walk_forward_mean_cagr: float = 0.0
    walk_forward_worst_drawdown: float = 0.0
    walk_forward_window_count: int = 0
    regime_worst_excess: float = 0.0
    regime_scores: tuple["RegimePerformance", ...] = field(default_factory=tuple)

    @property
    def full_excess(self) -> float:
        return self.full_result.total_return - self.full_result.benchmark_return

    @property
    def full_cagr(self) -> float:
        return self.full_result.cagr

    @property
    def full_sharpe(self) -> float:
        return self.full_result.sharpe

    @property
    def full_drawdown(self) -> float:
        return self.full_result.max_drawdown

    @property
    def trade_count(self) -> int:
        return self.full_result.trade_count


@dataclass(frozen=True)
class MomentumSearchResult(_SearchResultBase):
    pass


@dataclass(frozen=True)
class SatelliteSearchResult(_SearchResultBase):
    satellite_weight: float = 0.0


def default_momentum_search_configs(
    min_hold_days: int = 0,
) -> list[MonthlyMomentumRotationConfig]:
    configs: list[MonthlyMomentumRotationConfig] = []
    for lookback_days in (189, 252, 315):
        for trend_days in (150, 200, 252):
            for top_n in (1, 2, 3):
                for skip_days in (0, 21):
                    configs.append(
                        MonthlyMomentumRotationConfig(
                            lookback_days=lookback_days,
                            trend_days=trend_days,
                            top_n=top_n,
                            skip_days=skip_days,
                            rebalance_days=21,
                            min_hold_days=min_hold_days,
                        )
                    )
    return configs


def default_satellite_weights() -> list[float]:
    return [round(step / 20, 2) for step in range(0, 21)]


def _validate_search_inputs(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    split_ratio: float,
) -> None:
    if prices.empty:
        raise ValueError("prices must not be empty")
    if benchmark.empty:
        raise ValueError("benchmark must not be empty")
    if not 0.5 <= split_ratio < 1.0:
        raise ValueError("split_ratio must be between 0.5 and 1.0")


@dataclass(frozen=True)
class _SearchContext:
    train_end_date: pd.Timestamp
    test_start_date: pd.Timestamp
    walk_forward_windows: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]
    regime_segments: tuple[MarketRegimeSegment, ...]


def _build_search_context(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    split_ratio: float,
    walk_forward_train_size: int | None,
    walk_forward_test_size: int | None,
    walk_forward_step_size: int | None,
    regime_lookback_days: int,
    regime_min_segment_days: int,
) -> _SearchContext:
    index_length = len(prices.index)
    train_end_date, test_start_date = _split_period_boundaries(
        prices.index, split_ratio
    )
    walk_forward_windows = _rolling_walk_forward_boundaries(
        prices.index,
        train_size=_resolved_walk_forward_train_size(
            index_length, walk_forward_train_size, walk_forward_test_size
        ),
        test_size=_resolved_walk_forward_test_size(
            index_length, walk_forward_test_size
        ),
        step_size=_resolved_walk_forward_step_size(
            index_length, walk_forward_step_size, walk_forward_test_size
        ),
    )
    regime_segments = _derive_market_regime_segments(
        benchmark,
        lookback_days=regime_lookback_days,
        min_segment_days=regime_min_segment_days,
    )
    return _SearchContext(
        train_end_date=train_end_date,
        test_start_date=test_start_date,
        walk_forward_windows=walk_forward_windows,
        regime_segments=regime_segments,
    )


def _evaluate_result_metrics(
    result: BacktestResult,
    prices: pd.DataFrame,
    ctx: _SearchContext,
) -> dict:
    train_metrics = _summarize_period(
        result.equity_curve,
        result.benchmark_curve,
        prices.index[0],
        ctx.train_end_date,
    )
    test_metrics = _summarize_period(
        result.equity_curve,
        result.benchmark_curve,
        ctx.test_start_date,
        prices.index[-1],
    )
    walk_forward_metrics = _summarize_walk_forward_windows(
        result.equity_curve,
        result.benchmark_curve,
        ctx.walk_forward_windows,
    )
    regime_scores = _summarize_regime_performance(
        result.equity_curve,
        result.benchmark_curve,
        ctx.regime_segments,
    )
    return {
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "walk_forward_metrics": walk_forward_metrics,
        "regime_scores": regime_scores,
    }


def _build_search_result_fields(metrics: dict) -> dict:
    train_m = metrics["train_metrics"]
    test_m = metrics["test_metrics"]
    wf_m = metrics["walk_forward_metrics"]
    regime_scores = metrics["regime_scores"]
    return {
        "train_excess": train_m["total_return"] - train_m["benchmark_return"],
        "test_excess": test_m["total_return"] - test_m["benchmark_return"],
        "train_cagr": train_m["cagr"],
        "test_cagr": test_m["cagr"],
        "test_sharpe": test_m["sharpe"],
        "train_drawdown": train_m["max_drawdown"],
        "test_drawdown": test_m["max_drawdown"],
        "walk_forward_mean_excess": wf_m["mean_excess"],
        "walk_forward_worst_excess": wf_m["worst_excess"],
        "walk_forward_mean_cagr": wf_m["mean_cagr"],
        "walk_forward_worst_drawdown": wf_m["worst_drawdown"],
        "walk_forward_window_count": wf_m["window_count"],
        "regime_worst_excess": _worst_regime_excess(regime_scores),
        "regime_scores": regime_scores,
    }


def search_momentum_candidates(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    configs: Sequence[MonthlyMomentumRotationConfig] | None = None,
    split_ratio: float = 0.7,
    transaction_cost_per_trade: float = 0.0,
    transaction_cost_bps: float = 2.0,
    walk_forward_train_size: int | None = None,
    walk_forward_test_size: int | None = None,
    walk_forward_step_size: int | None = None,
    regime_lookback_days: int = 63,
    regime_min_segment_days: int = 21,
) -> list[MomentumSearchResult]:
    _validate_search_inputs(prices, benchmark, split_ratio)

    candidate_configs = (
        list(configs) if configs is not None else default_momentum_search_configs()
    )
    if not candidate_configs:
        raise ValueError("configs must not be empty")

    ctx = _build_search_context(
        prices,
        benchmark,
        split_ratio,
        walk_forward_train_size,
        walk_forward_test_size,
        walk_forward_step_size,
        regime_lookback_days,
        regime_min_segment_days,
    )

    ranked_results: list[MomentumSearchResult] = []
    for config in candidate_configs:
        full_result = run_backtest(
            prices,
            benchmark,
            MonthlyMomentumRotationStrategy(config),
            transaction_cost_per_trade=transaction_cost_per_trade,
            transaction_cost_bps=transaction_cost_bps,
        )
        metrics = _evaluate_result_metrics(full_result, prices, ctx)
        fields = _build_search_result_fields(metrics)
        ranked_results.append(
            MomentumSearchResult(config=config, full_result=full_result, **fields)
        )

    return sorted(ranked_results, key=_ranking_key, reverse=True)


def search_satellite_candidates(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    configs: Sequence[MonthlyMomentumRotationConfig] | None = None,
    satellite_weights: Sequence[float] | None = None,
    split_ratio: float = 0.7,
    transaction_cost_per_trade: float = 0.0,
    transaction_cost_bps: float = 2.0,
    walk_forward_train_size: int | None = None,
    walk_forward_test_size: int | None = None,
    walk_forward_step_size: int | None = None,
    regime_lookback_days: int = 63,
    regime_min_segment_days: int = 21,
) -> list[SatelliteSearchResult]:
    _validate_search_inputs(prices, benchmark, split_ratio)

    candidate_configs = (
        list(configs) if configs is not None else default_momentum_search_configs()
    )
    if not candidate_configs:
        raise ValueError("configs must not be empty")

    candidate_weights = (
        list(satellite_weights)
        if satellite_weights is not None
        else default_satellite_weights()
    )
    if not candidate_weights:
        raise ValueError("satellite_weights must not be empty")

    ctx = _build_search_context(
        prices,
        benchmark,
        split_ratio,
        walk_forward_train_size,
        walk_forward_test_size,
        walk_forward_step_size,
        regime_lookback_days,
        regime_min_segment_days,
    )

    ranked_results: list[SatelliteSearchResult] = []
    for config in candidate_configs:
        strategy_result = run_backtest(
            prices,
            benchmark,
            MonthlyMomentumRotationStrategy(config),
            transaction_cost_per_trade=transaction_cost_per_trade,
            transaction_cost_bps=transaction_cost_bps,
        )
        for satellite_weight in candidate_weights:
            blended_result = _blend_with_benchmark(strategy_result, satellite_weight)
            metrics = _evaluate_result_metrics(blended_result, prices, ctx)
            fields = _build_search_result_fields(metrics)
            ranked_results.append(
                SatelliteSearchResult(
                    config=config,
                    satellite_weight=satellite_weight,
                    full_result=blended_result,
                    **fields,
                )
            )

    return sorted(ranked_results, key=_ranking_key, reverse=True)


def _ranking_key(result: _SearchResultBase) -> tuple[float, ...]:
    return (
        result.walk_forward_worst_excess,
        result.walk_forward_mean_excess,
        result.regime_worst_excess,
        result.test_excess,
        result.test_sharpe,
        result.train_excess,
        result.full_excess,
    )
