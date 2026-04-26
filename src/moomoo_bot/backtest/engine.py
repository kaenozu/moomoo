"""Backtest engine module.

Purpose: Run strategy backtests and compute performance metrics.
Related: backtest/__init__.py, strategy modules.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import sqrt

import pandas as pd

from moomoo_bot.strategy.base import Strategy, TradeDecision


DEFAULT_TRANSACTION_COST_PER_TRADE = 0.0
DEFAULT_TRANSACTION_COST_BPS = 2.0


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.Series
    benchmark_curve: pd.Series
    trade_count: int
    transaction_costs: float
    total_return: float
    benchmark_return: float
    cagr: float
    benchmark_cagr: float
    volatility: float
    sharpe: float
    max_drawdown: float
    outperformance: float
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown_duration_days: int = 0

    def summary(self) -> dict[str, float]:
        return {
            "total_return": self.total_return,
            "benchmark_return": self.benchmark_return,
            "cagr": self.cagr,
            "benchmark_cagr": self.benchmark_cagr,
            "volatility": self.volatility,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "calmar": self.calmar,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_duration_days": float(self.max_drawdown_duration_days),
            "outperformance": self.outperformance,
            "trade_count": float(self.trade_count),
            "transaction_costs": self.transaction_costs,
        }


def blend_result_with_benchmark(
    strategy_result: BacktestResult, satellite_weight: float
) -> BacktestResult:
    if not 0.0 <= satellite_weight <= 1.0:
        raise ValueError("satellite_weight must be between 0.0 and 1.0")

    strategy_returns = strategy_result.equity_curve.pct_change().fillna(0.0)
    benchmark_returns = strategy_result.benchmark_curve.pct_change().fillna(0.0)
    blended_returns = (
        satellite_weight * strategy_returns
        + (1.0 - satellite_weight) * benchmark_returns
    )
    blended_equity = (1.0 + blended_returns).cumprod()
    blended_equity.name = "equity"

    benchmark_return = float(
        strategy_result.benchmark_curve.iloc[-1]
        / strategy_result.benchmark_curve.iloc[0]
        - 1.0
    )
    total_return = float(blended_equity.iloc[-1] / blended_equity.iloc[0] - 1.0)
    volatility = (
        float(blended_returns.iloc[1:].std(ddof=0) * sqrt(252))
        if len(blended_returns) > 1
        else 0.0
    )

    sortino = _sortino_ratio(blended_returns.iloc[1:])
    calmar = _calmar_ratio(
        _annualized_return(blended_equity), _max_drawdown(blended_equity)
    )
    max_dd_duration = _max_drawdown_duration_days(blended_equity)

    return BacktestResult(
        equity_curve=blended_equity,
        benchmark_curve=strategy_result.benchmark_curve,
        trade_count=strategy_result.trade_count,
        transaction_costs=strategy_result.transaction_costs,
        total_return=total_return,
        benchmark_return=benchmark_return,
        cagr=_annualized_return(blended_equity),
        benchmark_cagr=_annualized_return(strategy_result.benchmark_curve),
        volatility=volatility,
        sharpe=_sharpe_ratio(blended_returns.iloc[1:]),
        max_drawdown=_max_drawdown(blended_equity),
        outperformance=total_return - benchmark_return,
        sortino=sortino,
        calmar=calmar,
        max_drawdown_duration_days=max_dd_duration,
    )


def run_backtest(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    strategy: Strategy,
    transaction_cost_per_trade: float = DEFAULT_TRANSACTION_COST_PER_TRADE,
    transaction_cost_bps: float = DEFAULT_TRANSACTION_COST_BPS,
) -> BacktestResult:
    if prices.empty:
        raise ValueError("prices must not be empty")
    if benchmark.empty:
        raise ValueError("benchmark must not be empty")
    if transaction_cost_per_trade < 0.0:
        raise ValueError("transaction_cost_per_trade must not be negative")
    if transaction_cost_bps < 0.0:
        raise ValueError("transaction_cost_bps must not be negative")

    reset_strategy = getattr(strategy, "reset", None)
    if callable(reset_strategy):
        reset_strategy()

    prices = prices.sort_index()
    benchmark = benchmark.sort_index().reindex(prices.index).ffill()
    if benchmark.isna().any():
        raise ValueError("benchmark contains missing values after alignment")

    portfolio_returns: list[float] = []
    benchmark_returns: list[float] = []
    equity_values = [1.0]
    benchmark_values = [1.0]
    trade_count = 0
    previous_weights: dict[str, float] = {}
    transaction_costs_paid = 0.0

    dates = prices.index
    for index in range(1, len(dates)):
        current_date = dates[index - 1]
        next_date = dates[index]
        history = prices.iloc[:index]
        current_equity = equity_values[-1]
        decision: TradeDecision = strategy.decide(history, current_date)
        if decision.target_weights != previous_weights:
            order_count, turnover = _order_level_turnover(
                previous_weights, decision.target_weights
            )
            if order_count > 0:
                trade_count += order_count
                trade_cost = (order_count * transaction_cost_per_trade) + (
                    current_equity * turnover * (transaction_cost_bps / 10000.0)
                )
                transaction_costs_paid += trade_cost
                current_equity -= trade_cost
            previous_weights = dict(decision.target_weights)

        next_returns = prices.loc[next_date].div(prices.loc[current_date]).sub(1.0)
        portfolio_return = sum(
            decision.target_weights.get(symbol, 0.0) * float(next_returns[symbol])
            for symbol in prices.columns
        )
        benchmark_return = float(
            benchmark.loc[next_date] / benchmark.loc[current_date] - 1.0
        )

        portfolio_returns.append(portfolio_return)
        benchmark_returns.append(benchmark_return)
        equity_values.append(current_equity * (1.0 + portfolio_return))
        benchmark_values.append(benchmark_values[-1] * (1.0 + benchmark_return))

    equity_curve = pd.Series(equity_values, index=dates, name="equity")
    benchmark_curve = pd.Series(benchmark_values, index=dates, name="benchmark")
    portfolio_return_series = pd.Series(
        portfolio_returns, index=dates[1:], name="portfolio_return"
    )

    total_return = float(equity_curve.iloc[-1] / equity_curve.iloc[0] - 1.0)
    benchmark_total_return = float(
        benchmark_curve.iloc[-1] / benchmark_curve.iloc[0] - 1.0
    )
    cagr = _annualized_return(equity_curve)
    benchmark_cagr = _annualized_return(benchmark_curve)
    volatility = (
        float(portfolio_return_series.std(ddof=0) * sqrt(252))
        if len(portfolio_return_series) > 1
        else 0.0
    )
    sharpe = _sharpe_ratio(portfolio_return_series)
    sortino = _sortino_ratio(portfolio_return_series)
    max_drawdown = _max_drawdown(equity_curve)
    max_dd_duration = _max_drawdown_duration_days(equity_curve)
    calmar = _calmar_ratio(cagr, max_drawdown)

    return BacktestResult(
        equity_curve=equity_curve,
        benchmark_curve=benchmark_curve,
        trade_count=trade_count,
        transaction_costs=transaction_costs_paid,
        total_return=total_return,
        benchmark_return=benchmark_total_return,
        cagr=cagr,
        benchmark_cagr=benchmark_cagr,
        volatility=volatility,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        outperformance=total_return - benchmark_total_return,
        sortino=sortino,
        calmar=calmar,
        max_drawdown_duration_days=max_dd_duration,
    )


def annualized_return(curve: pd.Series) -> float:
    if len(curve) < 2:
        return 0.0
    elapsed_days = (curve.index[-1] - curve.index[0]).days
    if elapsed_days <= 0:
        return 0.0
    years = elapsed_days / 365.25
    starting_value = float(curve.iloc[0])
    ending_value = float(curve.iloc[-1])
    if starting_value <= 0.0:
        return 0.0
    if ending_value <= 0.0:
        return 0.0
    return float((ending_value / starting_value) ** (1.0 / years) - 1.0)


def sharpe_ratio(returns: pd.Series) -> float:
    if len(returns) < 2:
        return 0.0
    stdev = float(returns.std(ddof=0))
    if stdev == 0.0:
        return 0.0
    risk_free_rate: float = 0.0
    excess_returns = returns.mean() - risk_free_rate / 252
    return float((excess_returns / stdev) * sqrt(252))


def max_drawdown(curve: pd.Series) -> float:
    running_max = curve.cummax()
    drawdown = curve.div(running_max).sub(1.0)
    return float(drawdown.min())


def sortino_ratio(returns: pd.Series) -> float:
    if len(returns) < 2:
        return 0.0
    downside = returns.copy()
    downside[downside > 0.0] = 0.0
    downside_dev = float(downside.std(ddof=0))
    if downside_dev == 0.0:
        return 0.0
    excess_returns = returns.mean()
    return float((excess_returns / downside_dev) * sqrt(252))


def calmar_ratio(cagr: float, max_drawdown: float) -> float:
    if abs(max_drawdown) < 1e-9:
        return 0.0 if max_drawdown >= 0 else float("inf")
    return float(cagr / abs(max_drawdown))


def max_drawdown_duration_days(curve: pd.Series) -> int:
    running_max = curve.cummax()
    drawdown = curve.div(running_max).sub(1.0)
    is_in_drawdown = drawdown < 0.0

    if not is_in_drawdown.any():
        return 0

    groups = (is_in_drawdown != is_in_drawdown.shift()).cumsum()
    return (
        int(is_in_drawdown[is_in_drawdown].groupby(groups).size().max())
        if is_in_drawdown.any()
        else 0
    )


def _order_level_turnover(
    previous_weights: dict[str, float], current_weights: dict[str, float]
) -> tuple[int, float]:
    order_count = 0
    turnover = 0.0
    symbols = set(previous_weights) | set(current_weights)
    for symbol in symbols:
        delta = abs(
            float(current_weights.get(symbol, 0.0))
            - float(previous_weights.get(symbol, 0.0))
        )
        if delta <= 1e-12:
            continue
        order_count += 1
        turnover += delta
    return order_count, turnover


_annualized_return = annualized_return
_sharpe_ratio = sharpe_ratio
_max_drawdown = max_drawdown
_sortino_ratio = sortino_ratio
_calmar_ratio = calmar_ratio
_max_drawdown_duration_days = max_drawdown_duration_days


# ---------------------------------------------------------------------------
# Walk-forward, cost stress, regime classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train_start: object
    train_end: object
    test_start: object
    test_end: object
    result: BacktestResult


@dataclass(frozen=True)
class WalkForwardResult:
    train_period_days: int
    test_period_days: int
    folds: list[WalkForwardFold]
    out_of_sample_cagr: float
    out_of_sample_max_drawdown: float
    out_of_sample_sharpe: float
    winning_fold_pct: float

    def summary(self) -> dict[str, float]:
        return {
            "out_of_sample_cagr": self.out_of_sample_cagr,
            "out_of_sample_max_drawdown": self.out_of_sample_max_drawdown,
            "out_of_sample_sharpe": self.out_of_sample_sharpe,
            "winning_fold_pct": self.winning_fold_pct,
            "fold_count": float(len(self.folds)),
            "train_period_days": float(self.train_period_days),
            "test_period_days": float(self.test_period_days),
        }


def run_walk_forward_backtest(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    strategy_factory: Callable[[], Strategy],
    train_period_days: int = 504,
    test_period_days: int = 126,
    step_days: int | None = None,
    transaction_cost_bps: float = DEFAULT_TRANSACTION_COST_BPS,
) -> WalkForwardResult:
    """Run rolling walk-forward backtest.

    Each fold trains on train_period_days and evaluates on the next test_period_days.
    A fresh strategy instance is created for each fold via strategy_factory.
    """
    step = step_days if step_days is not None else test_period_days
    prices = prices.sort_index()
    benchmark = benchmark.sort_index().reindex(prices.index).ffill()
    dates = prices.index
    total_days = len(dates)
    min_required = train_period_days + test_period_days
    if total_days < min_required:
        raise ValueError(
            f"Not enough data: need {min_required} rows, got {total_days}"
        )

    folds: list[WalkForwardFold] = []
    fold_index = 0
    start_pos = 0
    while start_pos + min_required <= total_days:
        train_end_pos = start_pos + train_period_days
        test_end_pos = train_end_pos + test_period_days
        if test_end_pos > total_days:
            break

        train_prices = prices.iloc[start_pos:train_end_pos]
        train_benchmark = benchmark.iloc[start_pos:train_end_pos]
        test_prices = prices.iloc[train_end_pos - 1 : test_end_pos]
        test_benchmark = benchmark.iloc[train_end_pos - 1 : test_end_pos]

        strategy = strategy_factory()
        reset_fn = getattr(strategy, "reset", None)
        if callable(reset_fn):
            reset_fn()
        if len(train_prices) > 0:
            for i in range(len(train_prices)):
                strategy.decide(train_prices.iloc[: i + 1], train_prices.index[i])

        if len(test_prices) < 2:
            start_pos += step
            fold_index += 1
            continue

        test_result = run_backtest(
            test_prices,
            test_benchmark,
            strategy,
            transaction_cost_bps=transaction_cost_bps,
        )
        folds.append(
            WalkForwardFold(
                fold_index=fold_index,
                train_start=dates[start_pos],
                train_end=dates[train_end_pos - 1],
                test_start=dates[train_end_pos],
                test_end=dates[min(test_end_pos - 1, total_days - 1)],
                result=test_result,
            )
        )
        start_pos += step
        fold_index += 1

    if not folds:
        raise ValueError("No complete walk-forward folds could be generated")

    oos_returns_all: list[float] = []
    for fold in folds:
        ret_series = fold.result.equity_curve.pct_change().dropna()
        oos_returns_all.extend(ret_series.tolist())

    oos_returns = pd.Series(oos_returns_all)
    oos_cagr = sum(f.result.total_return for f in folds) / len(folds)
    oos_max_dd = min(f.result.max_drawdown for f in folds)
    oos_sharpe = _sharpe_ratio(oos_returns) if len(oos_returns) > 1 else 0.0
    winning_folds = sum(1 for f in folds if f.result.outperformance >= 0.0)
    winning_fold_pct = winning_folds / len(folds) if folds else 0.0

    return WalkForwardResult(
        train_period_days=train_period_days,
        test_period_days=test_period_days,
        folds=folds,
        out_of_sample_cagr=oos_cagr,
        out_of_sample_max_drawdown=oos_max_dd,
        out_of_sample_sharpe=oos_sharpe,
        winning_fold_pct=winning_fold_pct,
    )


def run_cost_stress_analysis(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    strategy_factory: Callable[[], Strategy],
    base_bps: float = DEFAULT_TRANSACTION_COST_BPS,
    multipliers: list[float] | None = None,
) -> dict[float, BacktestResult]:
    """Run backtests at multiple cost multipliers to assess robustness to friction."""
    if multipliers is None:
        multipliers = [1.0, 1.5, 2.0, 3.0]
    results: dict[float, BacktestResult] = {}
    for m in multipliers:
        strategy = strategy_factory()
        results[m] = run_backtest(
            prices,
            benchmark,
            strategy,
            transaction_cost_bps=base_bps * m,
        )
    return results


def run_sensitivity_sweep(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    strategy_factory: Callable[[object], Strategy],
    param_values: list[object],
    transaction_cost_bps: float = DEFAULT_TRANSACTION_COST_BPS,
) -> dict[object, BacktestResult]:
    """Run backtests for a list of parameter values and return results keyed by value.

    strategy_factory receives each param value and must return a fully configured Strategy.
    """
    results: dict[object, BacktestResult] = {}
    for val in param_values:
        strategy = strategy_factory(val)
        results[val] = run_backtest(
            prices,
            benchmark,
            strategy,
            transaction_cost_bps=transaction_cost_bps,
        )
    return results


def classify_regimes(benchmark: pd.Series, trend_days: int = 200) -> pd.Series:
    """Classify each date as 'bull', 'bear', or 'neutral' based on price vs rolling mean.

    Returns a Series of string labels aligned to the benchmark index.
    Requires at least trend_days rows; earlier rows are labeled 'neutral'.
    """
    ma = benchmark.rolling(trend_days, min_periods=trend_days).mean()
    regime = pd.Series("neutral", index=benchmark.index, dtype=object)
    regime[benchmark > ma] = "bull"
    regime[(benchmark < ma) & ma.notna()] = "bear"
    return regime
