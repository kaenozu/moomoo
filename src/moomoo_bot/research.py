"""Strategy research and candidate search module.

Purpose: Search and rank momentum strategy candidates and satellite blends.
Related: backtest.py, strategy/momentum.py.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def search_momentum_candidates(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    configs: Sequence[MonthlyMomentumRotationConfig] | None = None,
    split_ratio: float = 0.7,
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

    ranked_results: list[MomentumSearchResult] = []
    for config in candidate_configs:
        full_result = run_backtest(
            prices, benchmark, MonthlyMomentumRotationStrategy(config)
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
            )
        )

    return sorted(
        ranked_results,
        key=lambda result: (
            result.test_excess,
            result.test_sharpe,
            result.train_excess,
            result.full_excess,
        ),
        reverse=True,
    )


def search_satellite_candidates(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    configs: Sequence[MonthlyMomentumRotationConfig] | None = None,
    satellite_weights: Sequence[float] | None = None,
    split_ratio: float = 0.7,
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

    ranked_results: list[SatelliteSearchResult] = []
    for config in candidate_configs:
        strategy_result = run_backtest(
            prices, benchmark, MonthlyMomentumRotationStrategy(config)
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
                )
            )

    return sorted(
        ranked_results,
        key=lambda result: (
            result.test_excess,
            result.test_sharpe,
            result.train_excess,
            result.full_excess,
        ),
        reverse=True,
    )


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


def _blend_with_benchmark(
    strategy_result: BacktestResult, satellite_weight: float
) -> BacktestResult:
    return blend_result_with_benchmark(strategy_result, satellite_weight)


_annualized_return = annualized_return
_sharpe_ratio = sharpe_ratio
_max_drawdown = max_drawdown
