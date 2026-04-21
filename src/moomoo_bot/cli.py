from __future__ import annotations

from pathlib import Path
from time import sleep

import pandas as pd
import typer
from moomoo import Session, TrdEnv
from rich.console import Console
from rich.table import Table

from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.broker.paper import MoomooPaperTradeClient
from moomoo_bot.backtest import BacktestResult, make_demo_prices, run_backtest
from moomoo_bot.config import get_settings
from moomoo_bot.money import convert_capital_to_usd
from moomoo_bot.paper import PaperPlan, build_paper_plan, build_paper_rebalance_orders
from moomoo_bot.risk import (
    RiskState,
    build_liquidation_orders,
    build_stop_loss_take_profit_orders,
    detect_market_shock,
    update_drawdown_state,
)
from moomoo_bot.research import (
    default_momentum_search_configs,
    default_satellite_weights,
    search_momentum_candidates,
    search_satellite_candidates,
)
from moomoo_bot.strategy.momentum import MonthlyMomentumRotationConfig, MonthlyMomentumRotationStrategy

app = typer.Typer(add_completion=False, help="Moomoo bot CLI")
console = Console()


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


def _trade_mode_label(trd_env: TrdEnv) -> str:
    return "live" if trd_env == TrdEnv.REAL else "paper"


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
    table.add_row("Live max position weight", _format_percent(settings.live_max_position_weight))
    table.add_row("Max drawdown", _format_percent(settings.max_drawdown_pct))
    table.add_row("Market shock drop", _format_percent(settings.market_shock_drop_pct))
    table.add_row("Stop loss", _format_percent(settings.stop_loss_pct))
    table.add_row("Take profit", _format_percent(settings.take_profit_pct))
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
            raise typer.BadParameter(
                "benchmark_csv is required when the benchmark symbol is not included in prices_csv",
                param_name="benchmark_csv",
            )

    if price_frame.empty:
        raise typer.BadParameter("No tradable symbols found in the price frame.")

    candidate_configs = default_momentum_search_configs(min_hold_days=resolved_min_hold_days)
    candidate_weights = [resolved_satellite_weight] if resolved_satellite_weight >= 0.0 else default_satellite_weights()
    candidates = search_satellite_candidates(
        price_frame,
        benchmark_series,
        configs=candidate_configs,
        satellite_weights=candidate_weights,
    )
    ranked_candidates = sorted(candidates, key=lambda result: result.full_excess, reverse=True)
    best = ranked_candidates[0]

    strategy_description = (
        f"lookback={best.config.lookback_days}, trend={best.config.trend_days}, top_n={best.config.top_n}, "
        f"skip={best.config.skip_days}, rebalance={best.config.rebalance_days}, min_hold={best.config.min_hold_days}"
    )
    _render_backtest_result(
        best.full_result,
        benchmark_label,
        ", ".join(price_frame.columns),
        satellite_weight=best.satellite_weight,
        strategy_description=strategy_description,
    )
    _render_satellite_results(
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
        price_frame, benchmark_series = client.fetch_price_panel(
            selected_symbols,
            benchmark_label,
            history_days=history_days,
        )
        if len(price_frame) < settings.warmup_window:
            raise typer.BadParameter(
                f"Only {len(price_frame)} aligned rows were returned; need at least {settings.warmup_window}."
            )

        strategy = _build_monthly_strategy(settings)
        result = run_backtest(price_frame, benchmark_series, strategy)
        _render_snapshot(snapshot)
        _render_backtest_result(result, benchmark_label, ", ".join(price_frame.columns))
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
        price_frame, benchmark_series = client.fetch_price_panel(
            selected_symbols,
            benchmark_label,
            history_days=history_days,
        )
        results = search_momentum_candidates(
            price_frame,
            benchmark_series,
            configs=configs,
            split_ratio=split_ratio,
        )
        _render_research_results(results[:max_results], benchmark_label, ", ".join(selected_symbols), evaluated_count=len(results))
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
        price_frame, benchmark_series = client.fetch_price_panel(
            selected_symbols,
            benchmark_label,
            history_days=history_days,
        )
        results = search_satellite_candidates(
            price_frame,
            benchmark_series,
            configs=configs,
            satellite_weights=weights,
            split_ratio=split_ratio,
        )
        _render_satellite_results(
            results[:max_results],
            benchmark_label,
            ", ".join(selected_symbols),
            evaluated_count=len(results),
            config_count=len(configs),
            weight_count=len(weights),
        )
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
    if settings.execution_mode != "paper":
        raise typer.BadParameter("paper-run requires MOOMOO_BOT_EXECUTION_MODE=paper")

    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    paper_capital_input = capital if capital is not None else settings.initial_capital
    resolved_fx_rate = fx_jpy_per_usd if fx_jpy_per_usd is not None else settings.fx_jpy_per_usd
    paper_capital_usd = convert_capital_to_usd(paper_capital_input, settings.capital_currency, resolved_fx_rate)

    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        price_frame, benchmark_series = client.fetch_price_panel(
            selected_symbols,
            benchmark_label,
            history_days=history_days,
        )
        strategy = _build_monthly_strategy(settings)
        decision = strategy.decide(price_frame, price_frame.index[-1])
        plan = build_paper_plan(price_frame, decision, paper_capital_usd, minimum_order_value=minimum_order_value)
        _render_paper_plan(plan, benchmark_label, ", ".join(selected_symbols), benchmark_series)
        console.print(f"Capital input: {paper_capital_input:,.2f} {settings.capital_currency}")
        console.print(f"Capital used for sizing: {paper_capital_usd:,.2f} USD")
    finally:
        client.close()


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
    _run_one_shot_trade(
        settings=settings,
        command_name="paper-trade",
        trade_env=TrdEnv.SIMULATE,
        symbols=symbols,
        benchmark_symbol=benchmark_symbol,
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
    _run_one_shot_trade(
        settings=settings,
        command_name="live-trade",
        trade_env=TrdEnv.REAL,
        symbols=symbols,
        benchmark_symbol=benchmark_symbol,
        history_days=history_days,
        capital=capital,
        fx_jpy_per_usd=fx_jpy_per_usd,
        minimum_order_value=minimum_order_value,
        max_position_weight=settings.live_max_position_weight,
    )


def _run_one_shot_trade(
    *,
    settings,
    command_name: str,
    trade_env: TrdEnv,
    symbols: str | None,
    benchmark_symbol: str | None,
    history_days: int,
    capital: float | None,
    fx_jpy_per_usd: float | None,
    minimum_order_value: float,
    max_position_weight: float = 1.0,
) -> None:
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    paper_capital = capital if capital is not None else settings.initial_capital
    resolved_fx_rate = fx_jpy_per_usd if fx_jpy_per_usd is not None else settings.fx_jpy_per_usd
    paper_capital_usd = convert_capital_to_usd(paper_capital, settings.capital_currency, resolved_fx_rate)

    quote_client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    trade_client = MoomooPaperTradeClient(host=settings.opend_host, port=settings.opend_port, trd_env=trade_env)
    mode_label = _trade_mode_label(trade_env)
    try:
        position_frame = trade_client.get_position_frame()
        current_positions = _position_quantities(position_frame)
        symbol_universe = list(dict.fromkeys([*selected_symbols, *current_positions.keys()]))
        price_frame, benchmark_series = quote_client.fetch_price_panel(
            symbol_universe,
            benchmark_label,
            history_days=history_days,
        )
        latest_prices = {symbol: float(price_frame.iloc[-1][symbol]) for symbol in price_frame.columns}
        market_state = _fetch_market_state(quote_client, benchmark_label)
        market_open = _is_regular_market_open(market_state)
        account_value = trade_client.get_account_value() if capital is None else paper_capital_usd
        risk_state = RiskState(peak_account_value=account_value)
        shock_reason = detect_market_shock(benchmark_series, settings.market_shock_drop_pct)
        if shock_reason:
            console.print(f"Risk stop: {shock_reason}")
            _render_risk_orders([], current_positions, "Risk Stop Orders")
            return

        drawdown_reason = update_drawdown_state(account_value, risk_state, settings.max_drawdown_pct)
        if drawdown_reason:
            liquidation_orders = build_liquidation_orders(current_positions, latest_prices, drawdown_reason)
            _render_paper_plan(
                PaperPlan(as_of=price_frame.index[-1], capital=account_value, reason=drawdown_reason, allocations=[], cash_remaining=account_value),
                benchmark_label,
                ", ".join(symbol_universe),
                benchmark_series,
            )
            if capital is None:
                console.print(f"Capital input: {account_value:,.2f} USD (from {mode_label} account)")
            else:
                console.print(f"Capital input: {capital:,.2f} {settings.capital_currency}")
                console.print(f"Capital used for sizing: {account_value:,.2f} USD")
            _render_risk_orders(liquidation_orders, current_positions, "Risk Stop Orders")
            console.print(f"Submitting {mode_label} risk stop liquidation orders...")
            for instruction in liquidation_orders:
                response = trade_client.submit_order(instruction)
                _render_order_response(instruction, response)
            return

        strategy = _build_monthly_strategy(settings)
        decision = strategy.decide(price_frame, price_frame.index[-1])
        plan = build_paper_plan(
            price_frame,
            decision,
            account_value,
            minimum_order_value=minimum_order_value,
            max_position_weight=max_position_weight,
        )
        instructions = build_paper_rebalance_orders(
            plan,
            current_positions=current_positions,
            latest_prices=latest_prices,
            market_open=market_open,
        )
        risk_orders = build_stop_loss_take_profit_orders(position_frame, latest_prices, settings.stop_loss_pct, settings.take_profit_pct)

        if risk_orders:
            _render_paper_trade_plan(plan, benchmark_label, ", ".join(symbol_universe), benchmark_series, current_positions, risk_orders)
            if capital is None:
                console.print(f"Capital input: {account_value:,.2f} USD (from {mode_label} account)")
            else:
                console.print(f"Capital input: {capital:,.2f} {settings.capital_currency}")
                console.print(f"Capital used for sizing: {account_value:,.2f} USD")
                if not market_open:
                    console.print(f"Market state: {market_state}; buy orders will use ETH session.")
            _render_risk_orders(risk_orders, current_positions, "Risk Exit Orders")
            console.print(f"Submitting {mode_label} risk exit orders...")
            for instruction in risk_orders:
                response = trade_client.submit_order(instruction)
                _render_order_response(instruction, response)
            return

        _render_paper_trade_plan(plan, benchmark_label, ", ".join(symbol_universe), benchmark_series, current_positions, instructions)
        if capital is None:
            console.print(f"Capital input: {account_value:,.2f} USD (from {mode_label} account)")
        else:
            console.print(f"Capital input: {capital:,.2f} {settings.capital_currency}")
            console.print(f"Capital used for sizing: {account_value:,.2f} USD")
        if not market_open:
            console.print(f"Market state: {market_state}; buy orders will use ETH session.")

        if not instructions:
            console.print("No paper orders were required.")
            return

        console.print(f"Submitting {mode_label} orders...")
        for instruction in instructions:
            response = trade_client.submit_order(instruction)
            _render_order_response(instruction, response)
    finally:
        trade_client.close()
        quote_client.close()


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

    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    paper_capital_input = capital if capital is not None else settings.initial_capital
    resolved_fx_rate = fx_jpy_per_usd if fx_jpy_per_usd is not None else settings.fx_jpy_per_usd
    paper_capital_usd = convert_capital_to_usd(paper_capital_input, settings.capital_currency, resolved_fx_rate)
    strategy = _build_monthly_strategy(settings)
    quote_client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    trade_client = MoomooPaperTradeClient(host=settings.opend_host, port=settings.opend_port)
    risk_state = RiskState()

    console.print(
        f"Starting auto-run monitor for {', '.join(selected_symbols)} vs {benchmark_label}; "
        f"polling every {poll_seconds} seconds."
    )
    console.print(f"Capital input: {paper_capital_input:,.2f} {settings.capital_currency}")
    console.print(f"Capital used for sizing: {paper_capital_usd:,.2f} USD")

    try:
        consecutive_failures = 0
        while True:
            try:
                price_frame, benchmark_series = quote_client.fetch_price_panel(
                    selected_symbols,
                    benchmark_label,
                    history_days=history_days,
                )
                position_frame = trade_client.get_position_frame()
                current_positions = _position_quantities(position_frame)
                symbol_universe = list(dict.fromkeys([*selected_symbols, *current_positions.keys()]))
                latest_prices = {symbol: float(price_frame.iloc[-1][symbol]) for symbol in price_frame.columns}
                account_value = trade_client.get_account_value()
                market_state = _fetch_market_state(quote_client, benchmark_label)
                market_open = _is_regular_market_open(market_state)

                shock_reason = detect_market_shock(benchmark_series, settings.market_shock_drop_pct)
                if shock_reason:
                    console.print(f"{price_frame.index[-1].date()}: risk stop active - {shock_reason}")
                    sleep(poll_seconds)
                    continue

                drawdown_reason = update_drawdown_state(account_value, risk_state, settings.max_drawdown_pct)
                if drawdown_reason:
                    liquidation_orders = build_liquidation_orders(
                        current_positions,
                        latest_prices,
                        drawdown_reason,
                        session=Session.NONE if market_open else Session.ETH,
                        fill_outside_rth=not market_open,
                    )
                    _render_risk_orders(liquidation_orders, current_positions, "Risk Stop Orders")
                    if liquidation_orders:
                        console.print("Submitting risk stop liquidation orders...")
                        for instruction in liquidation_orders:
                            response = trade_client.submit_order(instruction)
                            _render_order_response(instruction, response)
                    console.print(f"{price_frame.index[-1].date()}: trading halted - {drawdown_reason}")
                    break

                decision = strategy.decide(price_frame, price_frame.index[-1])
                plan = build_paper_plan(price_frame, decision, paper_capital_usd, minimum_order_value=minimum_order_value)
                instructions = build_paper_rebalance_orders(
                    plan,
                    current_positions=current_positions,
                    latest_prices=latest_prices,
                    market_open=market_open,
                )
                risk_orders = build_stop_loss_take_profit_orders(
                    position_frame,
                    latest_prices,
                    settings.stop_loss_pct,
                    settings.take_profit_pct,
                    session=Session.NONE if market_open else Session.ETH,
                    fill_outside_rth=not market_open,
                )

                if risk_orders:
                    _render_paper_trade_plan(plan, benchmark_label, ", ".join(symbol_universe), benchmark_series, current_positions, risk_orders)
                    console.print(f"Capital input: {paper_capital_input:,.2f} {settings.capital_currency}")
                    console.print(f"Capital used for sizing: {plan.capital:,.2f} USD")
                    if not market_open:
                        console.print(f"Market state: {market_state}; buy orders will use ETH session.")
                    _render_risk_orders(risk_orders, current_positions, "Risk Exit Orders")
                    console.print("Submitting risk exit orders...")
                    for instruction in risk_orders:
                        response = trade_client.submit_order(instruction)
                        _render_order_response(instruction, response)
                    sleep(poll_seconds)
                    continue

                if instructions:
                    _render_paper_trade_plan(plan, benchmark_label, ", ".join(symbol_universe), benchmark_series, current_positions, instructions)
                    console.print(f"Capital input: {paper_capital_input:,.2f} {settings.capital_currency}")
                    console.print(f"Capital used for sizing: {plan.capital:,.2f} USD")
                    if not market_open:
                        console.print(f"Market state: {market_state}; buy orders will use ETH session.")
                    console.print("Submitting paper orders...")
                    for instruction in instructions:
                        response = trade_client.submit_order(instruction)
                        _render_order_response(instruction, response)
                else:
                    console.print(f"{price_frame.index[-1].date()}: no rebalance required; monitoring only.")

                consecutive_failures = 0
                sleep(poll_seconds)
            except Exception as exc:
                consecutive_failures += 1
                wait_seconds = min(poll_seconds * (2 ** (consecutive_failures - 1)), poll_seconds * 8)
                console.print(f"auto-run cycle failed ({consecutive_failures}/{max_consecutive_failures}): {exc}")
                if consecutive_failures >= max_consecutive_failures:
                    console.print("auto-run stopped after repeated failures.")
                    break
                sleep(wait_seconds)
    finally:
        trade_client.close()
        quote_client.close()


def _parse_symbols(raw_symbols: str | None) -> list[str]:
    if not raw_symbols:
        return []
    return [symbol.strip() for symbol in raw_symbols.split(",") if symbol.strip()]


def _parse_weights(raw_weights: str | None) -> list[float]:
    if not raw_weights:
        return []

    weights: list[float] = []
    for raw_weight in raw_weights.split(","):
        raw_weight = raw_weight.strip()
        if not raw_weight:
            continue
        try:
            weight = float(raw_weight)
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid satellite weight: {raw_weight}") from exc
        if not 0.0 <= weight <= 1.0:
            raise typer.BadParameter("satellite weights must be between 0 and 1.")
        weights.append(weight)
    return weights


def _fetch_market_state(client: MoomooOpenDClient, benchmark_symbol: str) -> str:
    market_state_frame = client.fetch_market_state([benchmark_symbol])
    if market_state_frame.empty:
        raise RuntimeError(f"No market state returned for {benchmark_symbol}")
    market_state = str(market_state_frame.iloc[0].get("market_state", "")).strip().upper()
    if not market_state:
        raise RuntimeError(f"Market state returned no value for {benchmark_symbol}")
    return market_state


def _is_regular_market_open(market_state: str) -> bool:
    return market_state in {"MORNING", "AFTERNOON"}


def _load_price_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    return frame.apply(pd.to_numeric, errors="coerce").dropna(how="all")


def _load_benchmark_series(path: Path) -> pd.Series:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    if frame.shape[1] != 1:
        raise typer.BadParameter("benchmark_csv must contain exactly one column.")
    series = frame.iloc[:, 0].apply(pd.to_numeric, errors="coerce").dropna()
    series.name = "benchmark"
    return series


def _render_backtest_result(
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
        table.add_row("Satellite weight", _format_percent(satellite_weight))
    table.add_row("Total return", _format_percent(result.total_return))
    table.add_row("Benchmark return", _format_percent(result.benchmark_return))
    table.add_row("Outperformance", _format_percent(result.outperformance))
    table.add_row("CAGR", _format_percent(result.cagr))
    table.add_row("Benchmark CAGR", _format_percent(result.benchmark_cagr))
    table.add_row("Volatility", _format_percent(result.volatility))
    table.add_row("Sharpe", f"{result.sharpe:.2f}")
    table.add_row("Max drawdown", _format_percent(result.max_drawdown))
    table.add_row("Trades", str(result.trade_count))
    console.print(table)


def _render_snapshot(snapshot: pd.DataFrame) -> None:
    columns = [column for column in ("code", "name", "last_price", "update_time", "prev_close_price") if column in snapshot.columns]
    if not columns:
        return

    table = Table(title="Market Snapshot")
    for column in columns:
        table.add_column(column, style="cyan" if column == "code" else "white")

    for _, row in snapshot[columns].head(5).iterrows():
        table.add_row(*[str(row[column]) for column in columns])

    console.print(table)


def _render_research_results(results, benchmark_label: str, universe: str, evaluated_count: int | None = None) -> None:
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
            _format_percent(result.train_excess),
            _format_percent(result.test_excess),
            _format_percent(result.full_excess),
            _format_percent(result.test_cagr),
            _format_ratio(result.test_sharpe),
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


def _render_satellite_results(
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
            _format_percent(result.satellite_weight),
            _format_percent(result.train_excess),
            _format_percent(result.test_excess),
            _format_percent(result.full_excess),
            _format_percent(result.test_cagr),
            _format_ratio(result.test_sharpe),
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
            f"test excess={_format_percent(best_active.test_excess)}, test CAGR={_format_percent(best_active.test_cagr)}."
        )


def _render_paper_plan(plan: PaperPlan, benchmark_label: str, universe: str, benchmark_series: pd.Series) -> None:
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
            _format_percent(allocation.weight),
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


def _render_paper_trade_plan(
    plan: PaperPlan,
    benchmark_label: str,
    universe: str,
    benchmark_series: pd.Series,
    current_positions: dict[str, float],
    instructions,
) -> None:
    _render_paper_plan(plan, benchmark_label, universe, benchmark_series)

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


def _render_risk_orders(instructions, current_positions: dict[str, float], title: str) -> None:
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


def _render_order_response(instruction, response: pd.DataFrame) -> None:
    order_id = response.iloc[0].get("order_id") if not response.empty else None
    status = response.iloc[0].get("order_status") if not response.empty else None
    console.print(
        f"Submitted {instruction.side} {instruction.symbol} qty={instruction.quantity:.3f} "
        f"price={instruction.price:.2f} order_id={order_id} status={status}"
    )


def _build_monthly_strategy(settings, min_hold_days: int | None = None):
    return MonthlyMomentumRotationStrategy(
        MonthlyMomentumRotationConfig(
            lookback_days=settings.lookback_days,
            trend_days=settings.trend_days,
            top_n=settings.top_n,
            skip_days=settings.skip_days,
            rebalance_days=settings.rebalance_days,
            min_hold_days=min_hold_days if min_hold_days is not None else settings.min_hold_days,
        )
    )


def _position_quantities(position_frame: pd.DataFrame) -> dict[str, float]:
    positions: dict[str, float] = {}
    for _, row in position_frame.iterrows():
        code = str(row.get("code", "")).strip()
        if not code:
            continue
        qty = float(row.get("qty", 0.0) or 0.0)
        if qty > 0.0:
            positions[code] = qty
    return positions


def _format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _format_ratio(value: float) -> str:
    return f"{value:.2f}"


if __name__ == "__main__":
    app()
