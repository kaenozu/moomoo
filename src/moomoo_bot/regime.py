"""Market regime classification and walk-forward boundary helpers.

Purpose: Extract regime detection, walk-forward window construction,
         and period summarization logic from the research module.
Why: Keeps research.py focused on search orchestration; regime logic
     is reusable for diagnostics and reporting.
Related: research.py, backtest/engine.py, strategy/momentum.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from moomoo_bot.backtest.engine import (
    annualized_return as _annualized_return,
    sharpe_ratio as _sharpe_ratio,
    max_drawdown as _max_drawdown,
)


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
        labels.append(
            (current_date, _classify_market_regime(current_return, current_drawdown))
        )

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
