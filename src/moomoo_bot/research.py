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
    blend_result_with_benchmark,
    annualized_return,
    sharpe_ratio,
    max_drawdown,
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


@dataclass(frozen=True)
class MarketRegimeSegment:
    label: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    observation_count: int


@dataclass(frozen=True)
class RegimePerformance:
    label: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    observation_count: int
    excess_return: float
    strategy_cagr: float
    benchmark_cagr: float
    max_drawdown: float


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
    if prices.empty:
        raise ValueError("prices must not be empty")
    if benchmark.empty:
        raise ValueError("benchmark must not be empty")
    if not 0.5 <= split_ratio < 1.0:
        raise ValueError("split_ratio must be between 0.5 and 1.0")

    candidate_configs = (
        list(configs) if configs is not None else default_momentum_search_configs()
    )
    if not candidate_configs:
        raise ValueError("configs must not be empty")

    train_end_date, test_start_date = _split_period_boundaries(
        prices.index, split_ratio
    )
    walk_forward_windows = _rolling_walk_forward_boundaries(
        prices.index,
        train_size=_resolved_walk_forward_train_size(
            len(prices.index), walk_forward_train_size, walk_forward_test_size
        ),
        test_size=_resolved_walk_forward_test_size(
            len(prices.index), walk_forward_test_size
        ),
        step_size=_resolved_walk_forward_step_size(
            len(prices.index), walk_forward_step_size, walk_forward_test_size
        ),
    )
    regime_segments = _derive_market_regime_segments(
        benchmark,
        lookback_days=regime_lookback_days,
        min_segment_days=regime_min_segment_days,
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
        train_metrics = _summarize_period(
            full_result.equity_curve,
            full_result.benchmark_curve,
            prices.index[0],
            train_end_date,
        )
        test_metrics = _summarize_period(
            full_result.equity_curve,
            full_result.benchmark_curve,
            test_start_date,
            prices.index[-1],
        )
        walk_forward_metrics = _summarize_walk_forward_windows(
            full_result.equity_curve,
            full_result.benchmark_curve,
            walk_forward_windows,
        )
        regime_scores = _summarize_regime_performance(
            full_result.equity_curve,
            full_result.benchmark_curve,
            regime_segments,
        )

        ranked_results.append(
            MomentumSearchResult(
                config=config,
                full_result=full_result,
                train_excess=train_metrics["total_return"]
                - train_metrics["benchmark_return"],
                test_excess=test_metrics["total_return"]
                - test_metrics["benchmark_return"],
                train_cagr=train_metrics["cagr"],
                test_cagr=test_metrics["cagr"],
                test_sharpe=test_metrics["sharpe"],
                train_drawdown=train_metrics["max_drawdown"],
                test_drawdown=test_metrics["max_drawdown"],
                walk_forward_mean_excess=walk_forward_metrics["mean_excess"],
                walk_forward_worst_excess=walk_forward_metrics["worst_excess"],
                walk_forward_mean_cagr=walk_forward_metrics["mean_cagr"],
                walk_forward_worst_drawdown=walk_forward_metrics["worst_drawdown"],
                walk_forward_window_count=walk_forward_metrics["window_count"],
                regime_worst_excess=_worst_regime_excess(regime_scores),
                regime_scores=regime_scores,
            )
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
    if prices.empty:
        raise ValueError("prices must not be empty")
    if benchmark.empty:
        raise ValueError("benchmark must not be empty")
    if not 0.5 <= split_ratio < 1.0:
        raise ValueError("split_ratio must be between 0.5 and 1.0")

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

    train_end_date, test_start_date = _split_period_boundaries(
        prices.index, split_ratio
    )
    walk_forward_windows = _rolling_walk_forward_boundaries(
        prices.index,
        train_size=_resolved_walk_forward_train_size(
            len(prices.index), walk_forward_train_size, walk_forward_test_size
        ),
        test_size=_resolved_walk_forward_test_size(
            len(prices.index), walk_forward_test_size
        ),
        step_size=_resolved_walk_forward_step_size(
            len(prices.index), walk_forward_step_size, walk_forward_test_size
        ),
    )
    regime_segments = _derive_market_regime_segments(
        benchmark,
        lookback_days=regime_lookback_days,
        min_segment_days=regime_min_segment_days,
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
            train_metrics = _summarize_period(
                blended_result.equity_curve,
                blended_result.benchmark_curve,
                prices.index[0],
                train_end_date,
            )
            test_metrics = _summarize_period(
                blended_result.equity_curve,
                blended_result.benchmark_curve,
                test_start_date,
                prices.index[-1],
            )
            walk_forward_metrics = _summarize_walk_forward_windows(
                blended_result.equity_curve,
                blended_result.benchmark_curve,
                walk_forward_windows,
            )
            regime_scores = _summarize_regime_performance(
                blended_result.equity_curve,
                blended_result.benchmark_curve,
                regime_segments,
            )

            ranked_results.append(
                SatelliteSearchResult(
                    config=config,
                    satellite_weight=satellite_weight,
                    full_result=blended_result,
                    train_excess=train_metrics["total_return"]
                    - train_metrics["benchmark_return"],
                    test_excess=test_metrics["total_return"]
                    - test_metrics["benchmark_return"],
                    train_cagr=train_metrics["cagr"],
                    test_cagr=test_metrics["cagr"],
                    test_sharpe=test_metrics["sharpe"],
                    train_drawdown=train_metrics["max_drawdown"],
                    test_drawdown=test_metrics["max_drawdown"],
                    walk_forward_mean_excess=walk_forward_metrics["mean_excess"],
                    walk_forward_worst_excess=walk_forward_metrics["worst_excess"],
                    walk_forward_mean_cagr=walk_forward_metrics["mean_cagr"],
                    walk_forward_worst_drawdown=walk_forward_metrics["worst_drawdown"],
                    walk_forward_window_count=walk_forward_metrics["window_count"],
                    regime_worst_excess=_worst_regime_excess(regime_scores),
                    regime_scores=regime_scores,
                )
            )

    return sorted(ranked_results, key=_ranking_key, reverse=True)


def _summarize_period(
    equity_curve: pd.Series,
    benchmark_curve: pd.Series,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> dict[str, float]:
    period_equity = equity_curve.loc[start_date:end_date]
    period_benchmark = benchmark_curve.loc[start_date:end_date]
    if len(period_equity) < 2 or len(period_benchmark) < 2:
        raise ValueError("not enough data in period to summarize")

    return {
        "total_return": float(period_equity.iloc[-1] / period_equity.iloc[0] - 1.0),
        "benchmark_return": float(
            period_benchmark.iloc[-1] / period_benchmark.iloc[0] - 1.0
        ),
        "cagr": _annualized_return(period_equity),
        "benchmark_cagr": _annualized_return(period_benchmark),
        "sharpe": _sharpe_ratio(period_equity.pct_change().dropna()),
        "max_drawdown": _max_drawdown(period_equity),
    }


def _split_period_boundaries(
    index: pd.Index, split_ratio: float
) -> tuple[pd.Timestamp, pd.Timestamp]:
    split_index = int(len(index) * split_ratio)
    if split_index <= 1 or split_index >= len(index) - 1:
        raise ValueError("split_ratio leaves no room for both train and test periods")
    return pd.Timestamp(index[split_index - 1]), pd.Timestamp(index[split_index])


def _rolling_walk_forward_boundaries(
    index: pd.Index,
    train_size: int,
    test_size: int,
    step_size: int,
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    if train_size <= 1:
        raise ValueError("train_size must be greater than 1")
    if test_size <= 1:
        raise ValueError("test_size must be greater than 1")
    if step_size <= 0:
        raise ValueError("step_size must be positive")

    windows: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
    start_index = train_size
    while start_index + test_size <= len(index):
        windows.append(
            (
                pd.Timestamp(index[start_index - 1]),
                pd.Timestamp(index[start_index]),
                pd.Timestamp(index[start_index + test_size - 1]),
            )
        )
        start_index += step_size
    return windows


def _resolved_walk_forward_test_size(index_length: int, test_size: int | None) -> int:
    if test_size is not None:
        return test_size
    return max(21, min(126, index_length // 8))


def _resolved_walk_forward_train_size(
    index_length: int,
    train_size: int | None,
    test_size: int | None,
) -> int:
    if train_size is not None:
        return train_size
    resolved_test_size = _resolved_walk_forward_test_size(index_length, test_size)
    return max(resolved_test_size * 2, min(756, max(63, index_length // 2)))


def _resolved_walk_forward_step_size(
    index_length: int,
    step_size: int | None,
    test_size: int | None,
) -> int:
    if step_size is not None:
        return step_size
    return _resolved_walk_forward_test_size(index_length, test_size)


def _summarize_walk_forward_windows(
    equity_curve: pd.Series,
    benchmark_curve: pd.Series,
    windows: Sequence[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]],
) -> dict[str, float | int]:
    if not windows:
        return {
            "mean_excess": 0.0,
            "worst_excess": 0.0,
            "mean_cagr": 0.0,
            "worst_drawdown": 0.0,
            "window_count": 0,
        }

    period_metrics = [
        _summarize_period(equity_curve, benchmark_curve, test_start, test_end)
        for _, test_start, test_end in windows
    ]
    excess_values = [
        metrics["total_return"] - metrics["benchmark_return"]
        for metrics in period_metrics
    ]
    cagr_values = [metrics["cagr"] for metrics in period_metrics]
    drawdown_values = [metrics["max_drawdown"] for metrics in period_metrics]
    return {
        "mean_excess": float(sum(excess_values) / len(excess_values)),
        "worst_excess": float(min(excess_values)),
        "mean_cagr": float(sum(cagr_values) / len(cagr_values)),
        "worst_drawdown": float(min(drawdown_values)),
        "window_count": len(period_metrics),
    }


def _derive_market_regime_segments(
    benchmark: pd.Series,
    lookback_days: int = 63,
    min_segment_days: int = 21,
) -> tuple[MarketRegimeSegment, ...]:
    if benchmark.empty:
        return ()
    if lookback_days <= 1:
        raise ValueError("lookback_days must be greater than 1")
    if min_segment_days <= 1:
        raise ValueError("min_segment_days must be greater than 1")

    benchmark = benchmark.sort_index().dropna()
    running_max = benchmark.cummax()
    drawdown = benchmark.div(running_max).sub(1.0)
    rolling_return = benchmark.pct_change(lookback_days)

    labels: list[tuple[pd.Timestamp, str]] = []
    for current_date in benchmark.index[lookback_days:]:
        current_drawdown = float(drawdown.loc[current_date])
        current_return = float(rolling_return.loc[current_date])
        labels.append((current_date, _classify_market_regime(current_return, current_drawdown)))

    if not labels:
        return ()

    segments: list[MarketRegimeSegment] = []
    segment_start = labels[0][0]
    segment_label = labels[0][1]
    observation_count = 1
    previous_date = labels[0][0]

    for current_date, current_label in labels[1:]:
        if current_label == segment_label:
            observation_count += 1
            previous_date = current_date
            continue

        if observation_count >= min_segment_days:
            segments.append(
                MarketRegimeSegment(
                    label=segment_label,
                    start_date=segment_start,
                    end_date=previous_date,
                    observation_count=observation_count,
                )
            )
        segment_start = current_date
        segment_label = current_label
        observation_count = 1
        previous_date = current_date

    if observation_count >= min_segment_days:
        segments.append(
            MarketRegimeSegment(
                label=segment_label,
                start_date=segment_start,
                end_date=previous_date,
                observation_count=observation_count,
            )
        )
    return tuple(segments)


def _classify_market_regime(rolling_return: float, drawdown: float) -> str:
    if drawdown <= -0.18 or rolling_return <= -0.12:
        return "crash"
    if drawdown <= -0.05 and rolling_return >= 0.08:
        return "recovery"
    if abs(rolling_return) <= 0.03:
        return "sideways"
    if rolling_return >= 0.06:
        return "uptrend"
    return "downtrend"


def _summarize_regime_performance(
    equity_curve: pd.Series,
    benchmark_curve: pd.Series,
    segments: Sequence[MarketRegimeSegment],
) -> tuple[RegimePerformance, ...]:
    regime_scores: list[RegimePerformance] = []
    for segment in segments:
        period_metrics = _summarize_period(
            equity_curve,
            benchmark_curve,
            segment.start_date,
            segment.end_date,
        )
        regime_scores.append(
            RegimePerformance(
                label=segment.label,
                start_date=segment.start_date,
                end_date=segment.end_date,
                observation_count=segment.observation_count,
                excess_return=(
                    period_metrics["total_return"] - period_metrics["benchmark_return"]
                ),
                strategy_cagr=period_metrics["cagr"],
                benchmark_cagr=period_metrics["benchmark_cagr"],
                max_drawdown=period_metrics["max_drawdown"],
            )
        )
    return tuple(regime_scores)


def _worst_regime_excess(regime_scores: Sequence[RegimePerformance]) -> float:
    if not regime_scores:
        return 0.0
    return float(min(score.excess_return for score in regime_scores))


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


def _blend_with_benchmark(
    strategy_result: BacktestResult, satellite_weight: float
) -> BacktestResult:
    return blend_result_with_benchmark(strategy_result, satellite_weight)


_annualized_return = annualized_return
_sharpe_ratio = sharpe_ratio
_max_drawdown = max_drawdown
