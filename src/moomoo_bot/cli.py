"""CLI command module.

Purpose: Typer application entrypoint and command implementations.
Related: cli_commands.py, cli_helpers.py, cli_render.py, orchestrator.py.
"""

from __future__ import annotations

from pathlib import Path

import typer
from moomoo import TrdEnv
from rich.table import Table

from moomoo_bot.backtest import make_demo_prices, run_backtest
from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.cli_helpers import (
    build_monthly_strategy as _build_monthly_strategy,
    load_benchmark_series as _load_benchmark_series,
    load_price_frame as _load_price_frame,
    parse_symbols as _parse_symbols,
    parse_weights as _parse_weights,
)
from moomoo_bot.cli_render import (
    console,
    format_percent,
    render_backtest_result,
    render_research_results,
    render_satellite_results,
    render_snapshot,
)
from moomoo_bot.config import get_settings
from moomoo_bot.orchestrator import run_auto_monitor, run_one_shot_trade
from moomoo_bot.research import (
    default_momentum_search_configs,
    default_satellite_weights,
    search_momentum_candidates,
    search_satellite_candidates,
    run_walk_forward_test,
)

app = typer.Typer(add_completion=False, help="Moomoo bot CLI")


def _require_paper_mode(settings, command_name: str) -> None:
    if settings.execution_mode != "paper":
        raise typer.BadParameter(f"{command_name} requires MOOMOO_BOT_EXECUTION_MODE=paper")


def _require_live_mode(settings, command_name: str, confirm_live_trading: bool) -> None:
    if settings.execution_mode != "live":
        raise typer.BadParameter(f"{command_name} requires MOOMOO_BOT_EXECUTION_MODE=live")
    if not settings.allow_live_trading:
        raise typer.BadParameter(f"{command_name} requires MOOMOO_BOT_ALLOW_LIVE_TRADING=true")
    if not confirm_live_trading:
        raise typer.BadParameter(f"{command_name} requires --confirm-live-trading")


@app.command()
def status() -> None:
    settings = get_settings()
    table = Table(title="Moomoo Bot Status")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    table.add_row("OpenD host", settings.opend_host)
    table.add_row("OpenD port", str(settings.opend_port))
    table.add_row("Execution mode", settings.execution_mode)
    table.add_row("Live trading enabled", str(settings.allow_live_trading))
    table.add_row("Symbols", ", ".join(settings.symbol_list))
    table.add_row("Benchmark", settings.benchmark_symbol)
    table.add_row("Lookback days", str(settings.lookback_days))
    table.add_row("Trend days", str(settings.trend_days))
    table.add_row("Skip days", str(settings.skip_days))
    table.add_row("Rebalance days", str(settings.rebalance_days))
    table.add_row("Min holding days", str(settings.min_hold_days))
    table.add_row("Backtest min holding days", str(settings.backtest_min_hold_days))
    table.add_row("Backtest satellite weight", f"{settings.backtest_satellite_weight:.2f}")
    table.add_row("Backtest top results", str(settings.backtest_top_results))
    table.add_row("Top N", str(settings.top_n))
    table.add_row("Initial capital", f"{settings.initial_capital:,.2f}")
    table.add_row("Capital currency", settings.capital_currency)
    table.add_row("JPY/USD rate", f"{settings.fx_jpy_per_usd:,.2f}")
    table.add_row("Live max position weight", format_percent(settings.live_max_position_weight))
    table.add_row("Max drawdown", format_percent(settings.max_drawdown_pct))
    table.add_row("Market shock drop", format_percent(settings.market_shock_drop_pct))
    table.add_row("Stop loss", format_percent(settings.stop_loss_pct))
    table.add_row("Take profit", format_percent(settings.take_profit_pct))
    console.print(table)


@app.command()
def backtest(
    prices_csv: Path | None = typer.Option(None, help="CSV file with date index and symbol columns."),
    benchmark_csv: Path | None = typer.Option(None, help="Optional CSV file with a benchmark series."),
    symbols: str | None = typer.Option(None, help="Comma-separated symbols for demo data or fallback configuration."),
    benchmark_symbol: str | None = typer.Option(None, help="Benchmark label used in reports."),
    periods: int = typer.Option(504, min=30, help="Number of trading days for demo data."),
    seed: int = typer.Option(7, min=0, help="Random seed for demo data."),
    min_holding_days: int | None = typer.Option(None, min=0, help="Minimum holding period for each position in trading days."),
    satellite_weight: float | None = typer.Option(None, min=0.0, max=1.0, help="Active sleeve weight over the benchmark; omit to auto-search for the best blend."),
    top_results: int | None = typer.Option(None, min=1, max=20, help="Number of top candidate configurations to display."),
) -> None:
    settings = get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    resolved_min_hold_days = min_holding_days if min_holding_days is not None else settings.backtest_min_hold_days
    resolved_satellite_weight = satellite_weight if satellite_weight is not None else settings.backtest_satellite_weight
    resolved_top_results = top_results if top_results is not None else settings.backtest_top_results

    if prices_csv is None:
        price_frame, benchmark_series = make_demo_prices(selected_symbols, periods=periods, seed=seed)
    else:
        price_frame = _load_price_frame(prices_csv)
        if benchmark_csv is not None:
            benchmark_series = _load_benchmark_series(benchmark_csv)
        elif benchmark_label in price_frame.columns:
            benchmark_series = price_frame[benchmark_label].copy()
            price_frame = price_frame.drop(columns=[benchmark_label])
        else:
            raise typer.BadParameter("benchmark_csv is required when the benchmark symbol is not included in prices_csv")

    if price_frame.empty:
        raise typer.BadParameter("No tradable symbols found in the price frame.")

    candidate_configs = default_momentum_search_configs(min_hold_days=resolved_min_hold_days)
    candidate_weights = [resolved_satellite_weight] if resolved_satellite_weight >= 0.0 else default_satellite_weights()
    candidates = search_satellite_candidates(price_frame, benchmark_series, configs=candidate_configs, satellite_weights=candidate_weights)
    ranked_candidates = sorted(candidates, key=lambda result: result.full_excess, reverse=True)
    best = ranked_candidates[0]

    strategy_description = (
        f"lookback={best.config.lookback_days}, trend={best.config.trend_days}, top_n={best.config.top_n}, "
        f"skip={best.config.skip_days}, rebalance={best.config.rebalance_days}, min_hold={best.config.min_hold_days}"
    )
    render_backtest_result(
        best.full_result,
        benchmark_label,
        ", ".join(price_frame.columns),
        satellite_weight=best.satellite_weight,
        strategy_description=strategy_description,
    )
    render_satellite_results(
        ranked_candidates[:resolved_top_results],
        benchmark_label,
        ", ".join(price_frame.columns),
        evaluated_count=len(candidates),
        config_count=len(candidate_configs),
        weight_count=len(candidate_weights),
        title=f"Top {resolved_top_results} Backtest Candidates",
    )


@app.command()
def verify_api(
    symbols: str | None = typer.Option(None, help="Comma-separated US symbols to verify against OpenD."),
    benchmark_symbol: str | None = typer.Option(None, help="Benchmark symbol used in the real-data backtest."),
    history_days: int = typer.Option(2200, min=60, help="Calendar lookback window for historical data."),
) -> None:
    settings = get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol

    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        snapshot = client.fetch_market_snapshot([*selected_symbols, benchmark_label])
        price_frame, benchmark_series = client.fetch_price_panel(selected_symbols, benchmark_label, history_days=history_days)
        if len(price_frame) < settings.warmup_window:
            raise typer.BadParameter(f"Only {len(price_frame)} aligned rows were returned; need at least {settings.warmup_window}.")

        strategy = _build_monthly_strategy(settings)
        result = run_backtest(price_frame, benchmark_series, strategy)
        render_snapshot(snapshot)
        render_backtest_result(result, benchmark_label, ", ".join(price_frame.columns))
    finally:
        client.close()


@app.command()
def research(
    symbols: str | None = typer.Option(None, help="Comma-separated US symbols to search over."),
    benchmark_symbol: str | None = typer.Option(None, help="Benchmark symbol used for excess return comparison."),
    history_days: int = typer.Option(2200, min=60, help="Calendar lookback window for historical data."),
    split_ratio: float = typer.Option(0.7, min=0.5, max=0.95, help="Train/test split ratio used for ranking."),
    max_results: int = typer.Option(10, min=1, max=25, help="Maximum number of ranked strategies to display."),
) -> None:
    settings = get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    configs = default_momentum_search_configs()

    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        price_frame, benchmark_series = client.fetch_price_panel(selected_symbols, benchmark_label, history_days=history_days)
        results = search_momentum_candidates(price_frame, benchmark_series, configs=configs, split_ratio=split_ratio)
        render_research_results(results[:max_results], benchmark_label, ", ".join(selected_symbols), evaluated_count=len(results))
    finally:
        client.close()


@app.command()
def satellite(
    symbols: str | None = typer.Option(None, help="Comma-separated US symbols to search over."),
    benchmark_symbol: str | None = typer.Option(None, help="Benchmark symbol used for core allocation."),
    history_days: int = typer.Option(2200, min=60, help="Calendar lookback window for historical data."),
    split_ratio: float = typer.Option(0.7, min=0.5, max=0.95, help="Train/test split ratio used for ranking."),
    satellite_weights: str | None = typer.Option(None, help="Comma-separated satellite weights between 0 and 1."),
    max_results: int = typer.Option(10, min=1, max=100, help="Maximum number of ranked combinations to display."),
) -> None:
    settings = get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    weights = _parse_weights(satellite_weights) or default_satellite_weights()
    configs = default_momentum_search_configs()

    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        price_frame, benchmark_series = client.fetch_price_panel(selected_symbols, benchmark_label, history_days=history_days)
        results = search_satellite_candidates(price_frame, benchmark_series, configs=configs, satellite_weights=weights, split_ratio=split_ratio)
        render_satellite_results(results[:max_results], benchmark_label, ", ".join(selected_symbols), evaluated_count=len(results), config_count=len(configs), weight_count=len(weights))
    finally:
        client.close()


@app.command()
def walk_forward(
    symbols: str | None = typer.Option(None, help="Comma-separated US symbols to search over."),
    benchmark_symbol: str | None = typer.Option(None, help="Benchmark symbol used for core allocation."),
    history_days: int = typer.Option(2200, min=504, help="Calendar lookback window for historical data."),
    train_years: int = typer.Option(3, min=1, help="Years to use for training (optimization)."),
    test_years: int = typer.Option(1, min=1, help="Years to use for testing (out-of-sample)."),
) -> None:
    settings = get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol

    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        price_frame, benchmark_series = client.fetch_price_panel(selected_symbols, benchmark_label, history_days=history_days)
        if len(price_frame) < 504:
            raise typer.BadParameter(f"Only {len(price_frame)} aligned rows were returned; need at least 504.")

        results = run_walk_forward_test(
            price_frame,
            benchmark_series,
            lambda: _build_monthly_strategy(settings),
            train_years=train_years,
            test_years=test_years,
        )

        if not results:
            console.print("No walk-forward windows completed.")
            return

        table = Table(title=f"Walk-Forward Results ({train_years}y train / {test_years}y test)")
        table.add_column("Window", style="cyan", no_wrap=True)
        table.add_column("Start Date")
        table.add_column("End Date")
        table.add_column("Return")
        table.add_column("CAGR")
        table.add_column("Sharpe")
        table.add_column("Max DD")
        table.add_column("Trades")

        for idx, result in enumerate(results):
            start_date = result.equity_curve.index[0].strftime("%Y-%m-%d")
            end_date = result.equity_curve.index[-1].strftime("%Y-%m-%d")
            table.add_row(
                str(idx + 1),
                start_date,
                end_date,
                format_percent(result.total_return),
                format_percent(result.cagr),
                f"{result.sharpe:.2f}",
                format_percent(result.max_drawdown),
                str(result.trade_count),
            )

        console.print(table)

        avg_return = sum(r.total_return for r in results) / len(results)
        avg_cagr = sum(r.cagr for r in results) / len(results)
        avg_sharpe = sum(r.sharpe for r in results) / len(results)
        avg_drawdown = sum(r.max_drawdown for r in results) / len(results)
        total_trades = sum(r.trade_count for r in results)

        summary = Table(title="Walk-Forward Summary")
        summary.add_column("Metric", style="cyan")
        summary.add_column("Average")
        summary.add_row("Return", format_percent(avg_return))
        summary.add_row("CAGR", format_percent(avg_cagr))
        summary.add_row("Sharpe", f"{avg_sharpe:.2f}")
        summary.add_row("Max Drawdown", format_percent(avg_drawdown))
        summary.add_row("Total Trades", str(total_trades))
        console.print(summary)

    finally:
        client.close()


@app.command()
def paper_run(
    symbols: str | None = typer.Option(None, help="Comma-separated US symbols to trade."),
    benchmark_symbol: str | None = typer.Option(None, help="Benchmark symbol used for market data alignment."),
    history_days: int = typer.Option(2200, min=60, help="Calendar lookback window for historical data."),
    capital: float | None = typer.Option(None, min=5.0, help="Paper trading capital used to size allocations (JPY by default)."),
    fx_jpy_per_usd: float | None = typer.Option(None, min=0.01, help="JPY per USD used to convert paper capital for sizing."),
    minimum_order_value: float = typer.Option(5.0, min=0.0, help="Minimum order value used for fractional sizing."),
) -> None:
    settings = get_settings()
    _require_paper_mode(settings, "paper-run")
    run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=_parse_symbols(symbols) or settings.symbol_list,
        benchmark_symbol=benchmark_symbol or settings.benchmark_symbol,
        history_days=history_days,
        capital=capital,
        fx_jpy_per_usd=fx_jpy_per_usd,
        minimum_order_value=minimum_order_value,
    )


@app.command()
def paper_trade(
    symbols: str | None = typer.Option(None, help="Comma-separated US symbols to trade."),
    benchmark_symbol: str | None = typer.Option(None, help="Benchmark symbol used for market data alignment."),
    history_days: int = typer.Option(2200, min=60, help="Calendar lookback window for historical data."),
    capital: float | None = typer.Option(None, min=5.0, help="Override input capital used for sizing the paper rebalance (JPY by default)."),
    fx_jpy_per_usd: float | None = typer.Option(None, min=0.01, help="JPY per USD used to convert paper capital for sizing."),
    minimum_order_value: float = typer.Option(5.0, min=0.0, help="Minimum order value used for fractional sizing."),
) -> None:
    settings = get_settings()
    _require_paper_mode(settings, "paper-trade")
    run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=_parse_symbols(symbols) or settings.symbol_list,
        benchmark_symbol=benchmark_symbol or settings.benchmark_symbol,
        history_days=history_days,
        capital=capital,
        fx_jpy_per_usd=fx_jpy_per_usd,
        minimum_order_value=minimum_order_value,
    )


@app.command()
def live_trade(
    symbols: str | None = typer.Option(None, help="Comma-separated US symbols to trade."),
    benchmark_symbol: str | None = typer.Option(None, help="Benchmark symbol used for market data alignment."),
    history_days: int = typer.Option(2200, min=60, help="Calendar lookback window for historical data."),
    capital: float | None = typer.Option(None, min=5.0, help="Override input capital used for sizing the live rebalance (JPY by default)."),
    fx_jpy_per_usd: float | None = typer.Option(None, min=0.01, help="JPY per USD used to convert paper capital for sizing."),
    minimum_order_value: float = typer.Option(5.0, min=0.0, help="Minimum order value used for fractional sizing."),
    confirm_live_trading: bool = typer.Option(False, "--confirm-live-trading", help="Required safety confirmation for live order submission."),
) -> None:
    settings = get_settings()
    _require_live_mode(settings, "live-trade", confirm_live_trading)
    run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.REAL,
        symbols=_parse_symbols(symbols) or settings.symbol_list,
        benchmark_symbol=benchmark_symbol or settings.benchmark_symbol,
        history_days=history_days,
        capital=capital,
        fx_jpy_per_usd=fx_jpy_per_usd,
        minimum_order_value=minimum_order_value,
        max_position_weight=settings.live_max_position_weight,
    )


@app.command()
def auto_run(
    symbols: str | None = typer.Option(None, help="Comma-separated US symbols to trade."),
    benchmark_symbol: str | None = typer.Option(None, help="Benchmark symbol used for market data alignment."),
    history_days: int = typer.Option(2200, min=60, help="Calendar lookback window for historical data."),
    capital: float | None = typer.Option(None, min=5.0, help="Override input capital used for sizing the paper rebalance (JPY by default)."),
    fx_jpy_per_usd: float | None = typer.Option(None, min=0.01, help="JPY per USD used to convert paper capital for sizing."),
    minimum_order_value: float = typer.Option(5.0, min=0.0, help="Minimum order value used for fractional sizing."),
    poll_seconds: int = typer.Option(900, min=60, help="Seconds to wait between monitoring cycles."),
    max_consecutive_failures: int = typer.Option(5, min=1, help="Maximum consecutive failed cycles before auto-run stops."),
) -> None:
    settings = get_settings()
    _require_paper_mode(settings, "auto-run")
    run_auto_monitor(
        settings=settings,
        symbols=_parse_symbols(symbols) or settings.symbol_list,
        benchmark_symbol=benchmark_symbol or settings.benchmark_symbol,
        history_days=history_days,
        capital=capital,
        fx_jpy_per_usd=fx_jpy_per_usd,
        minimum_order_value=minimum_order_value,
        poll_seconds=poll_seconds,
        max_consecutive_failures=max_consecutive_failures,
    )


if __name__ == "__main__":
    app()
