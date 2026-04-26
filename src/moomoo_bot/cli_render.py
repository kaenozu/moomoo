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


def format_money(value: float) -> str:
    return f"{value:,.2f}"


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
    columns = [
        column
        for column in ("code", "name", "last_price", "update_time", "prev_close_price")
        if column in snapshot.columns
    ]
    if not columns:
        return

    table = Table(title="Market Snapshot")
    for column in columns:
        table.add_column(column, style="cyan" if column == "code" else "white")

    for _, row in snapshot[columns].head(5).iterrows():
        table.add_row(*[str(row[column]) for column in columns])

    console.print(table)


def render_execution_report(
    summary,
    recent_fills,
    recent_realizations,
    recent_orders,
    symbol_label: str | None = None,
) -> None:
    title = "Execution Audit"
    if symbol_label:
        title += f" ({symbol_label})"

    summary_table = Table(title=title)
    summary_table.add_column("Metric", style="cyan", no_wrap=True)
    summary_table.add_column("Value", style="white")
    summary_table.add_row("Orders", str(summary.order_count))
    summary_table.add_row("Pending orders", str(summary.pending_order_count))
    summary_table.add_row("Fills", str(summary.fill_count))
    summary_table.add_row("Buy fills", str(summary.buy_fill_count))
    summary_table.add_row("Sell fills", str(summary.sell_fill_count))
    summary_table.add_row("Open tax lots", str(summary.open_lot_count))
    summary_table.add_row("Realizations", str(summary.realization_count))
    summary_table.add_row("Total fees", format_money(summary.total_fees))
    summary_table.add_row("Total slippage", format_money(summary.total_slippage))
    summary_table.add_row("Realized PnL", format_money(summary.realized_pnl))
    summary_table.add_row("Last fill", str(summary.last_fill_at or "-"))
    summary_table.add_row("Last realization", str(summary.last_realization_at or "-"))
    console.print(summary_table)

    if recent_fills:
        fills_table = Table(title="Recent Fills")
        fills_table.add_column("Order ID", style="cyan", no_wrap=True)
        fills_table.add_column("Symbol")
        fills_table.add_column("Side")
        fills_table.add_column("Qty")
        fills_table.add_column("Fill")
        fills_table.add_column("Fee")
        fills_table.add_column("Slip")
        fills_table.add_column("Time")
        for fill in recent_fills:
            fills_table.add_row(
                str(fill.order_id),
                fill.symbol,
                fill.side,
                f"{fill.fill_quantity:.3f}",
                f"{fill.fill_price:.2f}",
                format_money(fill.fee_amount),
                format_money(fill.slippage_amount),
                str(fill.filled_at or "-"),
            )
        console.print(fills_table)

    if recent_realizations:
        realizations_table = Table(title="Recent Realizations")
        realizations_table.add_column("Order ID", style="cyan", no_wrap=True)
        realizations_table.add_column("Symbol")
        realizations_table.add_column("Qty")
        realizations_table.add_column("PnL")
        realizations_table.add_column("Fee")
        realizations_table.add_column("Slip")
        realizations_table.add_column("Closed At")
        for realization in recent_realizations:
            realizations_table.add_row(
                str(realization.sell_order_id or "-"),
                realization.symbol,
                f"{realization.quantity:.3f}",
                format_money(realization.realized_pnl or 0.0),
                format_money(realization.fee_amount),
                format_money(realization.slippage_amount),
                str(realization.closed_at or "-"),
            )
        console.print(realizations_table)

    if recent_orders:
        orders_table = Table(title="Recent Orders")
        orders_table.add_column("Order ID", style="cyan", no_wrap=True)
        orders_table.add_column("Symbol")
        orders_table.add_column("Side")
        orders_table.add_column("Qty")
        orders_table.add_column("Price")
        orders_table.add_column("Status")
        for order in recent_orders:
            orders_table.add_row(
                str(order.order_id or "-"),
                order.symbol,
                order.side,
                f"{order.quantity:.3f}",
                f"{order.price:.2f}",
                order.status,
            )
        console.print(orders_table)


def render_research_results(
    results, benchmark_label: str, universe: str, evaluated_count: int | None = None
) -> None:
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
    table.add_column("WF Mean")
    table.add_column("WF Worst")
    table.add_column("Regime Worst")
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
            format_percent(result.walk_forward_mean_excess),
            format_percent(result.walk_forward_worst_excess),
            format_percent(result.regime_worst_excess),
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
    table.add_column("WF Mean")
    table.add_column("WF Worst")
    table.add_column("Regime Worst")
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
            format_percent(result.walk_forward_mean_excess),
            format_percent(result.walk_forward_worst_excess),
            format_percent(result.regime_worst_excess),
            format_percent(result.full_excess),
            format_percent(result.test_cagr),
            format_ratio(result.test_sharpe),
            str(result.trade_count),
        )

    console.print(table)
    console.print(
        f"Evaluated {config_count} momentum configurations across {weight_count} satellite weights ({evaluated_count} combinations)."
    )

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
            f"wf worst={format_percent(best_active.walk_forward_worst_excess)}, "
            f"regime worst={format_percent(best_active.regime_worst_excess)}, "
            f"test CAGR={format_percent(best_active.test_cagr)}."
        )


def render_paper_plan(
    plan: PaperPlan, benchmark_label: str, universe: str, benchmark_series: pd.Series
) -> None:
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
    console.print(
        f"Benchmark last close: {float(benchmark_series.loc[: plan.as_of].iloc[-1]):.2f}"
    )


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


def render_risk_orders(
    instructions, current_positions: dict[str, float], title: str
) -> None:
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


def render_cost_stress_result(results: dict[float, BacktestResult]) -> None:
    """Render a table comparing backtest results across cost multipliers."""
    table = Table(title="Cost Stress Analysis")
    table.add_column("Cost Multiplier", justify="right")
    table.add_column("CAGR", justify="right")
    table.add_column("Max Drawdown", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("Calmar", justify="right")
    table.add_column("Total Return", justify="right")
    table.add_column("Outperformance", justify="right")
    for multiplier in sorted(results):
        r = results[multiplier]
        table.add_row(
            f"{multiplier:.1f}x",
            format_percent(r.cagr),
            format_percent(r.max_drawdown),
            format_ratio(r.sharpe),
            format_ratio(r.calmar),
            format_percent(r.total_return),
            format_percent(r.outperformance),
        )
    console.print(table)


def render_walk_forward_result(result) -> None:
    """Render walk-forward backtest results fold-by-fold plus summary."""
    from moomoo_bot.backtest import WalkForwardResult

    if not isinstance(result, WalkForwardResult):
        console.print("[red]Invalid walk-forward result object.[/red]")
        return

    fold_table = Table(title="Walk-Forward Folds (Out-of-Sample)")
    fold_table.add_column("Fold", justify="right")
    fold_table.add_column("Test Start")
    fold_table.add_column("Test End")
    fold_table.add_column("CAGR", justify="right")
    fold_table.add_column("Max DD", justify="right")
    fold_table.add_column("Sharpe", justify="right")
    fold_table.add_column("Outperf.", justify="right")
    for fold in result.folds:
        r = fold.result
        fold_table.add_row(
            str(fold.fold_index + 1),
            str(fold.test_start)[:10],
            str(fold.test_end)[:10],
            format_percent(r.cagr),
            format_percent(r.max_drawdown),
            format_ratio(r.sharpe),
            format_percent(r.outperformance),
        )
    console.print(fold_table)

    summary_table = Table(title="Walk-Forward Summary")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", justify="right")
    summary_table.add_row("Folds", str(len(result.folds)))
    summary_table.add_row("Train Period (days)", str(result.train_period_days))
    summary_table.add_row("Test Period (days)", str(result.test_period_days))
    summary_table.add_row("OOS CAGR (avg)", format_percent(result.out_of_sample_cagr))
    summary_table.add_row(
        "OOS Max Drawdown (worst)", format_percent(result.out_of_sample_max_drawdown)
    )
    summary_table.add_row(
        "OOS Sharpe (combined)", format_ratio(result.out_of_sample_sharpe)
    )
    summary_table.add_row("Winning Fold %", format_percent(result.winning_fold_pct))
    console.print(summary_table)


def render_performance_metrics(metrics: dict) -> None:
    """Render live-trading performance metrics from state store data."""
    table = Table(title="Live Performance Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    for key, value in metrics.items():
        if isinstance(value, float):
            if "pct" in key or "rate" in key or "ratio" in key or key.endswith("_pct"):
                formatted = format_percent(value)
            else:
                formatted = f"{value:.4f}"
        else:
            formatted = str(value)
        table.add_row(key.replace("_", " ").title(), formatted)
    console.print(table)
