"""Status and reporting CLI commands module.

Purpose: Status display, execution reports, and performance metrics.
         Uses deferred imports from moomoo_bot.cli for monkeypatch compatibility.
Related: _cli_app.py, cli.py, state.py.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from moomoo_bot._cli_app import app

import moomoo_bot.cli as _cli


@app.command()
def status() -> None:
    from moomoo_bot.cli_render import console, format_percent

    settings = _cli.get_settings()
    runtime_profile_drift = _cli.describe_runtime_profile_drift(settings)
    resolved_state_db_path = _cli.resolve_state_db_path(
        db_path=settings.state_db_path,
        execution_mode=settings.execution_mode,
    )
    table = Table(title="Moomoo Bot Status")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    table.add_row("OpenD host", settings.opend_host)
    table.add_row("OpenD port", str(settings.opend_port))
    table.add_row("Execution mode", settings.execution_mode)
    table.add_row("State DB", str(resolved_state_db_path))
    table.add_row(
        "Validated profile",
        "aligned" if not runtime_profile_drift else "custom override",
    )
    if runtime_profile_drift:
        table.add_row("Profile drift", ", ".join(runtime_profile_drift))
    table.add_row("Live trading enabled", str(settings.allow_live_trading))
    table.add_row("Symbols", ", ".join(settings.symbol_list))
    table.add_row("Benchmark", settings.benchmark_symbol)
    table.add_row("Lookback days", str(settings.lookback_days))
    table.add_row("Trend days", str(settings.trend_days))
    table.add_row("Skip days", str(settings.skip_days))
    table.add_row("Rebalance days", str(settings.rebalance_days))
    table.add_row("Min holding days", str(settings.min_hold_days))
    table.add_row("Backtest min holding days", str(settings.backtest_min_hold_days))
    table.add_row(
        "Backtest satellite weight", f"{settings.backtest_satellite_weight:.2f}"
    )
    table.add_row("Backtest top results", str(settings.backtest_top_results))
    table.add_row("Top N", str(settings.top_n))
    table.add_row("Satellite weight", format_percent(settings.satellite_weight))
    table.add_row("Initial capital", f"{settings.initial_capital:,.2f}")
    table.add_row("Capital currency", settings.capital_currency)
    table.add_row("JPY/USD rate", f"{settings.fx_jpy_per_usd:,.2f}")
    table.add_row(
        "Transaction cost per trade", f"{settings.transaction_cost_per_trade:.2f}"
    )
    table.add_row("Transaction cost bps", f"{settings.transaction_cost_bps:.2f}")
    table.add_row(
        "Live max position weight", format_percent(settings.live_max_position_weight)
    )
    table.add_row("Max drawdown", format_percent(settings.max_drawdown_pct))
    table.add_row("Market shock drop", format_percent(settings.market_shock_drop_pct))
    table.add_row("Stop loss", format_percent(settings.stop_loss_pct))
    table.add_row("Take profit", format_percent(settings.take_profit_pct))
    table.add_row("Max daily orders", str(settings.max_daily_orders))
    table.add_row("Equity retention days", str(settings.equity_retention_days))
    console.print(table)


@app.command()
def execution_report(
    symbol: str | None = typer.Option(
        None, help="Optional symbol filter for execution audit output."
    ),
    fills_limit: int = typer.Option(
        10, min=1, max=100, help="Maximum number of recent fills to display."
    ),
    realizations_limit: int = typer.Option(
        10,
        min=1,
        max=100,
        help="Maximum number of recent tax-lot realizations to display.",
    ),
    orders_limit: int = typer.Option(
        10, min=1, max=100, help="Maximum number of recent orders to display."
    ),
    db_path: Path | None = typer.Option(
        None, help="Optional path to an alternate state.db file."
    ),
) -> None:
    settings = _cli.get_settings()
    state_store = _cli.StateStore(
        db_path=db_path or settings.state_db_path,
        execution_mode=settings.execution_mode,
    )
    try:
        summary = state_store.summarize_execution_activity(symbol=symbol)
        recent_fills = state_store.get_execution_fills(symbol=symbol, limit=fills_limit)
        recent_realizations = state_store.get_tax_lot_realizations(
            symbol=symbol,
            limit=realizations_limit,
        )
        recent_orders = state_store.load_recent_orders(limit=orders_limit)
        _cli.render_execution_report(
            summary,
            recent_fills,
            recent_realizations,
            recent_orders,
            symbol_label=symbol,
        )
    finally:
        state_store.close()


@app.command()
def performance(
    lookback_trades: int = typer.Option(
        50, min=1, help="Number of recent realized trades to analyze."
    ),
) -> None:
    """Show live-trading performance metrics from the state store."""
    import pandas as _pd

    from moomoo_bot.cli_render import console, render_performance_metrics

    settings = _cli.get_settings()
    state_path = _cli.resolve_state_db_path(settings)
    state_store = _cli.StateStore(state_path)
    try:
        realizations = state_store.get_recent_realizations(lookback_trades)
        equity_curve_rows = state_store.get_equity_curve()

        if not realizations:
            console.print("[yellow]No realized trades found in state store.[/yellow]")
            return

        pnls = [r.realized_pnl for r in realizations if r.realized_pnl is not None]
        opening_prices = [
            r.opening_price for r in realizations if r.opening_price is not None
        ]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / len(pnls) if pnls else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        avg_opening = (
            sum(opening_prices) / len(opening_prices) if opening_prices else 1.0
        )
        ev_ratio = avg_pnl / avg_opening if avg_opening > 0 else 0.0

        fill_summary = state_store.summarize_execution_activity()
        avg_slippage_bps = (
            (fill_summary.total_slippage / fill_summary.fill_count * 10000.0)
            if fill_summary.fill_count > 0
            else 0.0
        )

        metrics: dict = {
            "trade_count": len(realizations),
            "win_rate_pct": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "pl_ratio": pl_ratio,
            "avg_pnl": avg_pnl,
            "ev_ratio": ev_ratio,
            "avg_slippage_bps": avg_slippage_bps,
        }

        if equity_curve_rows:
            eq_values = [r.account_value for r in equity_curve_rows]
            eq = _pd.Series(eq_values, dtype=float)
            running_max = eq.cummax()
            dd = float((eq.div(running_max).sub(1.0)).min())
            metrics["max_drawdown_pct"] = dd

        render_performance_metrics(metrics)
    finally:
        state_store.close()
