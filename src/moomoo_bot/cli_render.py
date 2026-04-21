"""CLI rendering utilities module.

Purpose: Rich table/formatted output helpers for CLI commands.
Related: cli.py, backtest module.
"""

from __future__ import annotations

import pandas as pd
from rich.console import Console
from rich.table import Table

from moomoo_bot.backtest import BacktestResult
from moomoo_bot.paper import PaperPlan

console = Console()


def format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def format_ratio(value: float) -> str:
    return f"{value:.2f}"


_format_percent = format_percent
_format_ratio = format_ratio


def render_backtest_result(
    result: BacktestResult,
    benchmark_label: str,
    universe: str,
    satellite_weight: float | None = None,
    strategy_description: str | None = None,
) -> None:
    table = Table(title="Backtest Result")
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    table.add_row("Universe", universe)
    table.add_row("Benchmark", benchmark_label)
    if strategy_description is not None:
        table.add_row("Strategy", strategy_description)
    if satellite_weight is not None:
        table.add_row("Satellite weight", format_percent(satellite_weight))
    table.add_row("Total return", format_percent(result.total_return))
    table.add_row("Benchmark return", format_percent(result.benchmark_return))
    table.add_row("Outperformance", format_percent(result.outperformance))
    table.add_row("CAGR", format_percent(result.cagr))
    table.add_row("Benchmark CAGR", format_percent(result.benchmark_cagr))
    table.add_row("Volatility", format_percent(result.volatility))
    table.add_row("Sharpe", f"{result.sharpe:.2f}")
    table.add_row("Max drawdown", format_percent(result.max_drawdown))
    table.add_row("Trades", str(result.trade_count))
    console.print(table)


def render_snapshot(snapshot: pd.DataFrame) -> None:
    columns = [column for column in ("code", "name", "last_price", "update_time", "prev_close_price") if column in snapshot.columns]
    if not columns:
        return

    table = Table(title="Market Snapshot")
    for column in columns:
        table.add_column(column, style="cyan" if column == "code" else "white")

    for _, row in snapshot[columns].head(5).iterrows():
        table.add_row(*[str(row[column]) for column in columns])

    console.print(table)


def render_research_results(results, benchmark_label: str, universe: str, evaluated_count: int | None = None) -> None:
    if not results:
        console.print("No strategies matched the search criteria.")
        return

    table = Table(title="Momentum Search Results")
    table.add_column("Rank", style="cyan", no_wrap=True)
    table.add_column("Lookback")
    table.add_column("Trend")
    table.add_column("Top N")
    table.add_column("Skip")
    table.add_column("Train Excess")
    table.add_column("Test Excess")
    table.add_column("Full Excess")
    table.add_column("Test CAGR")
    table.add_column("Test Sharpe")
    table.add_column("Trades")

    for index, result in enumerate(results, start=1):
        config = result.config
        table.add_row(
            str(index),
            str(config.lookback_days),
            str(config.trend_days),
            str(config.top_n),
            str(config.skip_days),
            format_percent(result.train_excess),
            format_percent(result.test_excess),
            format_percent(result.full_excess),
            format_percent(result.test_cagr),
            format_ratio(result.test_sharpe),
            str(result.trade_count),
        )

    console.print(table)
    if evaluated_count is not None:
        console.print(f"Evaluated {evaluated_count} momentum configurations.")
    best = results[0]
    console.print(
        f"Best candidate on {universe} vs {benchmark_label}: "
        f"lookback={best.config.lookback_days}, trend={best.config.trend_days}, "
        f"top_n={best.config.top_n}, skip={best.config.skip_days}, rebalance={best.config.rebalance_days}."
    )


def render_satellite_results(
    results,
    benchmark_label: str,
    universe: str,
    evaluated_count: int,
    config_count: int,
    weight_count: int,
    title: str = "Satellite Search Results",
) -> None:
    if not results:
        console.print("No satellite combinations matched the search criteria.")
        return

    table = Table(title=title)
    table.add_column("Rank", style="cyan", no_wrap=True)
    table.add_column("Lookback")
    table.add_column("Trend")
    table.add_column("Top N")
    table.add_column("Skip")
    table.add_column("Satellite")
    table.add_column("Train Excess")
    table.add_column("Test Excess")
    table.add_column("Full Excess")
    table.add_column("Test CAGR")
    table.add_column("Test Sharpe")
    table.add_column("Trades")

    for index, result in enumerate(results, start=1):
        config = result.config
        table.add_row(
            str(index),
            str(config.lookback_days),
            str(config.trend_days),
            str(config.top_n),
            str(config.skip_days),
            format_percent(result.satellite_weight),
            format_percent(result.train_excess),
            format_percent(result.test_excess),
            format_percent(result.full_excess),
            format_percent(result.test_cagr),
            format_ratio(result.test_sharpe),
            str(result.trade_count),
        )

    console.print(table)
    console.print(f"Evaluated {config_count} momentum configurations across {weight_count} satellite weights ({evaluated_count} combinations).")

    best = results[0]
    console.print(
        f"Best satellite blend on {universe} vs {benchmark_label}: "
        f"satellite={best.satellite_weight:.0%}, benchmark={(1.0 - best.satellite_weight):.0%}, "
        f"lookback={best.config.lookback_days}, trend={best.config.trend_days}, "
        f"top_n={best.config.top_n}, skip={best.config.skip_days}, rebalance={best.config.rebalance_days}."
    )

    active_results = [result for result in results if result.satellite_weight > 0.0]
    if active_results:
        best_active = active_results[0]
        console.print(
            f"Best active sleeve among positive weights: satellite={best_active.satellite_weight:.0%}, "
            f"test excess={format_percent(best_active.test_excess)}, test CAGR={format_percent(best_active.test_cagr)}."
        )


def render_paper_plan(plan: PaperPlan, benchmark_label: str, universe: str, benchmark_series: pd.Series) -> None:
    table = Table(title="Paper Trading Plan")
    table.add_column("Symbol", style="cyan", no_wrap=True)
    table.add_column("Weight")
    table.add_column("Price")
    table.add_column("Target Value")
    table.add_column("Target Qty")
    table.add_column("Target Cost")

    for allocation in plan.allocations:
        table.add_row(
            allocation.symbol,
            format_percent(allocation.weight),
            f"{allocation.price:.2f}",
            f"{allocation.target_value:,.2f}",
            f"{allocation.target_quantity:.3f}",
            f"{allocation.target_cost:,.2f}",
        )

    console.print(table)
    console.print(f"Universe: {universe}")
    console.print(f"Benchmark: {benchmark_label}")
    console.print(f"Strategy date: {plan.as_of.date()}")
    console.print(f"Reason: {plan.reason}")
    console.print(f"Capital: {plan.capital:,.2f}")
    console.print(f"Cash remaining after sizing: {plan.cash_remaining:,.2f}")
    console.print(f"Benchmark last close: {float(benchmark_series.loc[:plan.as_of].iloc[-1]):.2f}")


def render_paper_trade_plan(
    plan: PaperPlan,
    benchmark_label: str,
    universe: str,
    benchmark_series: pd.Series,
    current_positions: dict[str, float],
    instructions,
) -> None:
    render_paper_plan(plan, benchmark_label, universe, benchmark_series)

    table = Table(title="Paper Rebalance Instructions")
    table.add_column("Side", style="cyan", no_wrap=True)
    table.add_column("Symbol")
    table.add_column("Qty")
    table.add_column("Price")
    table.add_column("Current Qty")

    for instruction in instructions:
        table.add_row(
            str(instruction.side),
            instruction.symbol,
            f"{instruction.quantity:.3f}",
            f"{instruction.price:.2f}",
            f"{current_positions.get(instruction.symbol, 0.0):.3f}",
        )

    console.print(table)


def render_risk_orders(instructions, current_positions: dict[str, float], title: str) -> None:
    if not instructions:
        console.print(f"{title}: none")
        return

    table = Table(title=title)
    table.add_column("Side", style="cyan", no_wrap=True)
    table.add_column("Symbol")
    table.add_column("Qty")
    table.add_column("Price")
    table.add_column("Current Qty")

    for instruction in instructions:
        table.add_row(
            str(instruction.side),
            instruction.symbol,
            f"{instruction.quantity:.3f}",
            f"{instruction.price:.2f}",
            f"{current_positions.get(instruction.symbol, 0.0):.3f}",
        )

    console.print(table)


def render_order_response(instruction, response: pd.DataFrame) -> None:
    order_id = response.iloc[0].get("order_id") if not response.empty else None
    status = response.iloc[0].get("order_status") if not response.empty else None
    console.print(
        f"Submitted {instruction.side} {instruction.symbol} qty={instruction.quantity:.3f} "
        f"price={instruction.price:.2f} order_id={order_id} status={status}"
    )