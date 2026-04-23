"""Backtest engine module.

Purpose: Run strategy backtests and compute performance metrics.
Related: backtest/__init__.py, strategy modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

import pandas as pd

from moomoo_bot.strategy.base import Strategy, TradeDecision


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

    def summary(self) -> dict[str, float]:
        return {
            "total_return": self.total_return,
            "benchmark_return": self.benchmark_return,
            "cagr": self.cagr,
            "benchmark_cagr": self.benchmark_cagr,
            "volatility": self.volatility,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
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
    )


def run_backtest(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    strategy: Strategy,
    transaction_cost_per_trade: float = 0.0,
    transaction_cost_bps: float = 0.0,
) -> BacktestResult:
    if prices.empty:
        raise ValueError("prices must not be empty")
    if benchmark.empty:
        raise ValueError("benchmark must not be empty")
    if transaction_cost_per_trade < 0.0:
        raise ValueError("transaction_cost_per_trade must not be negative")
    if transaction_cost_bps < 0.0:
        raise ValueError("transaction_cost_bps must not be negative")

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
        decision: TradeDecision = strategy.decide(history, current_date)
        if decision.target_weights != previous_weights:
            if decision.target_weights:
                trade_count += 1
                trade_cost = transaction_cost_per_trade + equity_values[-1] * (transaction_cost_bps / 10000.0)
                transaction_costs_paid += trade_cost
                equity_values[-1] -= trade_cost
            previous_weights = decision.target_weights

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
        equity_values.append(equity_values[-1] * (1.0 + portfolio_return))
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
    max_drawdown = _max_drawdown(equity_curve)

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


_annualized_return = annualized_return
_sharpe_ratio = sharpe_ratio
_max_drawdown = max_drawdown
