"""CLI command module.

Purpose: Typer application entrypoint and command implementations.
Related: cli_commands.py, cli_helpers.py, cli_render.py.
"""

from __future__ import annotations

from pathlib import Path
from time import sleep

import typer
from moomoo import Session, TrdEnv
from rich.table import Table

from moomoo_bot.backtest import make_demo_prices, run_backtest
from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.broker.paper import MoomooPaperTradeClient
from moomoo_bot.cli_helpers import (
    build_monthly_strategy as _build_monthly_strategy,
    fetch_market_state as _fetch_market_state,
    is_regular_market_open as _is_regular_market_open,
    load_benchmark_series as _load_benchmark_series,
    load_price_frame as _load_price_frame,
    parse_symbols as _parse_symbols,
    parse_weights as _parse_weights,
    position_quantities as _position_quantities,
    submit_orders_with_duplicate_guard as _submit_orders_with_duplicate_guard,
    trade_mode_label as _trade_mode_label,
)
from moomoo_bot.cli_render import (
    console,
    format_percent,
    render_backtest_result,
    render_order_response,
    render_paper_plan,
    render_paper_trade_plan,
    render_research_results,
    render_risk_orders,
    render_satellite_results,
    render_snapshot,
)
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

app = typer.Typer(add_completion=False, help="Moomoo bot CLI")


def _require_paper_mode(settings, command_name: str) -> None:
    if settings.execution_mode != "paper":
        raise typer.BadParameter(
            f"{command_name} requires MOOMOO_BOT_EXECUTION_MODE=paper"
        )


def _require_live_mode(settings, command_name: str, confirm_live_trading: bool) -> None:
    if settings.execution_mode != "live":
        raise typer.BadParameter(
            f"{command_name} requires MOOMOO_BOT_EXECUTION_MODE=live"
        )
    if not settings.allow_live_trading:
        raise typer.BadParameter(
            f"{command_name} requires MOOMOO_BOT_ALLOW_LIVE_TRADING=true"
        )
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
    table.add_row(
        "Backtest satellite weight", f"{settings.backtest_satellite_weight:.2f}"
    )
    table.add_row("Backtest top results", str(settings.backtest_top_results))
    table.add_row("Top N", str(settings.top_n))
    table.add_row("Initial capital", f"{settings.initial_capital:,.2f}")
    table.add_row("Capital currency", settings.capital_currency)
    table.add_row("JPY/USD rate", f"{settings.fx_jpy_per_usd:,.2f}")
    table.add_row(
        "Live max position weight", format_percent(settings.live_max_position_weight)
    )
    table.add_row("Max drawdown", format_percent(settings.max_drawdown_pct))
    table.add_row("Market shock drop", format_percent(settings.market_shock_drop_pct))
    table.add_row("Stop loss", format_percent(settings.stop_loss_pct))
    table.add_row("Take profit", format_percent(settings.take_profit_pct))
    console.print(table)


@app.command()
def backtest(
    prices_csv: Path | None = typer.Option(
        None, help="CSV file with date index and symbol columns."
    ),
    benchmark_csv: Path | None = typer.Option(
        None, help="Optional CSV file with a benchmark series."
    ),
    symbols: str | None = typer.Option(
        None, help="Comma-separated symbols for demo data or fallback configuration."
    ),
    benchmark_symbol: str | None = typer.Option(
        None, help="Benchmark label used in reports."
    ),
    periods: int = typer.Option(
        504, min=30, help="Number of trading days for demo data."
    ),
    seed: int = typer.Option(7, min=0, help="Random seed for demo data."),
    min_holding_days: int | None = typer.Option(
        None, min=0, help="Minimum holding period for each position in trading days."
    ),
    satellite_weight: float | None = typer.Option(
        None,
        min=0.0,
        max=1.0,
        help="Active sleeve weight over the benchmark; omit to auto-search for the best blend.",
    ),
    top_results: int | None = typer.Option(
        None, min=1, max=20, help="Number of top candidate configurations to display."
    ),
) -> None:
    settings = get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    resolved_min_hold_days = (
        min_holding_days
        if min_holding_days is not None
        else settings.backtest_min_hold_days
    )
    resolved_satellite_weight = (
        satellite_weight
        if satellite_weight is not None
        else settings.backtest_satellite_weight
    )
    resolved_top_results = (
        top_results if top_results is not None else settings.backtest_top_results
    )

    if prices_csv is None:
        price_frame, benchmark_series = make_demo_prices(
            selected_symbols, periods=periods, seed=seed
        )
    else:
        price_frame = _load_price_frame(prices_csv)
        if benchmark_csv is not None:
            benchmark_series = _load_benchmark_series(benchmark_csv)
        elif benchmark_label in price_frame.columns:
            benchmark_series = price_frame[benchmark_label].copy()
            price_frame = price_frame.drop(columns=[benchmark_label])
        else:
            raise typer.BadParameter(
                "benchmark_csv is required when the benchmark symbol is not included in prices_csv"
            )

    if price_frame.empty:
        raise typer.BadParameter("No tradable symbols found in the price frame.")

    candidate_configs = default_momentum_search_configs(
        min_hold_days=resolved_min_hold_days
    )
    candidate_weights = (
        [resolved_satellite_weight]
        if resolved_satellite_weight >= 0.0
        else default_satellite_weights()
    )
    candidates = search_satellite_candidates(
        price_frame,
        benchmark_series,
        configs=candidate_configs,
        satellite_weights=candidate_weights,
    )
    ranked_candidates = sorted(
        candidates, key=lambda result: result.full_excess, reverse=True
    )
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
    symbols: str | None = typer.Option(
        None, help="Comma-separated US symbols to verify against OpenD."
    ),
    benchmark_symbol: str | None = typer.Option(
        None, help="Benchmark symbol used in the real-data backtest."
    ),
    history_days: int = typer.Option(
        2200, min=60, help="Calendar lookback window for historical data."
    ),
) -> None:
    settings = get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol

    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        snapshot = client.fetch_market_snapshot([*selected_symbols, benchmark_label])
        price_frame, benchmark_series = client.fetch_price_panel(
            selected_symbols, benchmark_label, history_days=history_days
        )
        if len(price_frame) < settings.warmup_window:
            raise typer.BadParameter(
                f"Only {len(price_frame)} aligned rows were returned; need at least {settings.warmup_window}."
            )

        strategy = _build_monthly_strategy(settings)
        result = run_backtest(price_frame, benchmark_series, strategy)
        render_snapshot(snapshot)
        render_backtest_result(result, benchmark_label, ", ".join(price_frame.columns))
    finally:
        client.close()


@app.command()
def research(
    symbols: str | None = typer.Option(
        None, help="Comma-separated US symbols to search over."
    ),
    benchmark_symbol: str | None = typer.Option(
        None, help="Benchmark symbol used for excess return comparison."
    ),
    history_days: int = typer.Option(
        2200, min=60, help="Calendar lookback window for historical data."
    ),
    split_ratio: float = typer.Option(
        0.7, min=0.5, max=0.95, help="Train/test split ratio used for ranking."
    ),
    max_results: int = typer.Option(
        10, min=1, max=25, help="Maximum number of ranked strategies to display."
    ),
) -> None:
    settings = get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    configs = default_momentum_search_configs()

    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        price_frame, benchmark_series = client.fetch_price_panel(
            selected_symbols, benchmark_label, history_days=history_days
        )
        results = search_momentum_candidates(
            price_frame, benchmark_series, configs=configs, split_ratio=split_ratio
        )
        render_research_results(
            results[:max_results],
            benchmark_label,
            ", ".join(selected_symbols),
            evaluated_count=len(results),
        )
    finally:
        client.close()


@app.command()
def satellite(
    symbols: str | None = typer.Option(
        None, help="Comma-separated US symbols to search over."
    ),
    benchmark_symbol: str | None = typer.Option(
        None, help="Benchmark symbol used for core allocation."
    ),
    history_days: int = typer.Option(
        2200, min=60, help="Calendar lookback window for historical data."
    ),
    split_ratio: float = typer.Option(
        0.7, min=0.5, max=0.95, help="Train/test split ratio used for ranking."
    ),
    satellite_weights: str | None = typer.Option(
        None, help="Comma-separated satellite weights between 0 and 1."
    ),
    max_results: int = typer.Option(
        10, min=1, max=100, help="Maximum number of ranked combinations to display."
    ),
) -> None:
    settings = get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    weights = _parse_weights(satellite_weights) or default_satellite_weights()
    configs = default_momentum_search_configs()

    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        price_frame, benchmark_series = client.fetch_price_panel(
            selected_symbols, benchmark_label, history_days=history_days
        )
        results = search_satellite_candidates(
            price_frame,
            benchmark_series,
            configs=configs,
            satellite_weights=weights,
            split_ratio=split_ratio,
        )
        render_satellite_results(
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
    symbols: str | None = typer.Option(
        None, help="Comma-separated US symbols to trade."
    ),
    benchmark_symbol: str | None = typer.Option(
        None, help="Benchmark symbol used for market data alignment."
    ),
    history_days: int = typer.Option(
        2200, min=60, help="Calendar lookback window for historical data."
    ),
    capital: float | None = typer.Option(
        None,
        min=5.0,
        help="Paper trading capital used to size allocations (JPY by default).",
    ),
    fx_jpy_per_usd: float | None = typer.Option(
        None, min=0.01, help="JPY per USD used to convert paper capital for sizing."
    ),
    minimum_order_value: float = typer.Option(
        5.0, min=0.0, help="Minimum order value used for fractional sizing."
    ),
) -> None:
    settings = get_settings()
    _require_paper_mode(settings, "paper-run")
    _run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=symbols,
        benchmark_symbol=benchmark_symbol,
        history_days=history_days,
        capital=capital,
        fx_jpy_per_usd=fx_jpy_per_usd,
        minimum_order_value=minimum_order_value,
    )


@app.command()
def paper_trade(
    symbols: str | None = typer.Option(
        None, help="Comma-separated US symbols to trade."
    ),
    benchmark_symbol: str | None = typer.Option(
        None, help="Benchmark symbol used for market data alignment."
    ),
    history_days: int = typer.Option(
        2200, min=60, help="Calendar lookback window for historical data."
    ),
    capital: float | None = typer.Option(
        None,
        min=5.0,
        help="Override input capital used for sizing the paper rebalance (JPY by default).",
    ),
    fx_jpy_per_usd: float | None = typer.Option(
        None, min=0.01, help="JPY per USD used to convert paper capital for sizing."
    ),
    minimum_order_value: float = typer.Option(
        5.0, min=0.0, help="Minimum order value used for fractional sizing."
    ),
) -> None:
    settings = get_settings()
    _require_paper_mode(settings, "paper-trade")
    _run_one_shot_trade(
        settings=settings,
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
    symbols: str | None = typer.Option(
        None, help="Comma-separated US symbols to trade."
    ),
    benchmark_symbol: str | None = typer.Option(
        None, help="Benchmark symbol used for market data alignment."
    ),
    history_days: int = typer.Option(
        2200, min=60, help="Calendar lookback window for historical data."
    ),
    capital: float | None = typer.Option(
        None,
        min=5.0,
        help="Override input capital used for sizing the live rebalance (JPY by default).",
    ),
    fx_jpy_per_usd: float | None = typer.Option(
        None, min=0.01, help="JPY per USD used to convert paper capital for sizing."
    ),
    minimum_order_value: float = typer.Option(
        5.0, min=0.0, help="Minimum order value used for fractional sizing."
    ),
    confirm_live_trading: bool = typer.Option(
        False,
        "--confirm-live-trading",
        help="Required safety confirmation for live order submission.",
    ),
) -> None:
    settings = get_settings()
    _require_live_mode(settings, "live-trade", confirm_live_trading)
    _run_one_shot_trade(
        settings=settings,
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
    resolved_fx_rate = (
        fx_jpy_per_usd if fx_jpy_per_usd is not None else settings.fx_jpy_per_usd
    )
    paper_capital_usd = convert_capital_to_usd(
        paper_capital, settings.capital_currency, resolved_fx_rate
    )

    quote_client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    trade_client = MoomooPaperTradeClient(
        host=settings.opend_host, port=settings.opend_port, trd_env=trade_env
    )
    mode_label = _trade_mode_label(trade_env)
    try:
        position_frame = trade_client.get_position_frame()
        current_positions = _position_quantities(position_frame)
        symbol_universe = list(
            dict.fromkeys([*selected_symbols, *current_positions.keys()])
        )
        price_frame, benchmark_series = quote_client.fetch_price_panel(
            symbol_universe, benchmark_label, history_days=history_days
        )
        latest_prices = {
            symbol: float(price_frame.iloc[-1][symbol])
            for symbol in price_frame.columns
        }
        market_state = _fetch_market_state(quote_client, benchmark_label)
        market_open = _is_regular_market_open(market_state)
        account_value = (
            trade_client.get_account_value() if capital is None else paper_capital_usd
        )
        risk_state = RiskState(peak_account_value=account_value)
        shock_reason = detect_market_shock(
            benchmark_series, settings.market_shock_drop_pct
        )
        if shock_reason:
            console.print(f"Risk stop: {shock_reason}")
            render_risk_orders([], current_positions, "Risk Stop Orders")
            return

        drawdown_reason = update_drawdown_state(
            account_value, risk_state, settings.max_drawdown_pct
        )
        if drawdown_reason:
            liquidation_orders = build_liquidation_orders(
                current_positions, latest_prices, drawdown_reason
            )
            render_paper_plan(
                PaperPlan(
                    as_of=price_frame.index[-1],
                    capital=account_value,
                    reason=drawdown_reason,
                    allocations=[],
                    cash_remaining=account_value,
                ),
                benchmark_label,
                ", ".join(symbol_universe),
                benchmark_series,
            )
            if capital is None:
                console.print(
                    f"Capital input: {account_value:,.2f} USD (from {mode_label} account)"
                )
            else:
                console.print(
                    f"Capital input: {capital:,.2f} {settings.capital_currency}"
                )
                console.print(f"Capital used for sizing: {account_value:,.2f} USD")
            render_risk_orders(
                liquidation_orders, current_positions, "Risk Stop Orders"
            )
            console.print(f"Submitting {mode_label} risk stop liquidation orders...")
            _submit_orders_with_duplicate_guard(
                trade_client, liquidation_orders, mode_label, render_order_response
            )
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
        risk_orders = build_stop_loss_take_profit_orders(
            position_frame,
            latest_prices,
            settings.stop_loss_pct,
            settings.take_profit_pct,
        )

        if risk_orders:
            render_paper_trade_plan(
                plan,
                benchmark_label,
                ", ".join(symbol_universe),
                benchmark_series,
                current_positions,
                risk_orders,
            )
            if capital is None:
                console.print(
                    f"Capital input: {account_value:,.2f} USD (from {mode_label} account)"
                )
            else:
                console.print(
                    f"Capital input: {capital:,.2f} {settings.capital_currency}"
                )
                console.print(f"Capital used for sizing: {account_value:,.2f} USD")
            if not market_open:
                console.print(
                    f"Market state: {market_state}; buy orders will use ETH session."
                )
            render_risk_orders(risk_orders, current_positions, "Risk Exit Orders")
            console.print(f"Submitting {mode_label} risk exit orders...")
            _submit_orders_with_duplicate_guard(
                trade_client, risk_orders, mode_label, render_order_response
            )
            return

        render_paper_trade_plan(
            plan,
            benchmark_label,
            ", ".join(symbol_universe),
            benchmark_series,
            current_positions,
            instructions,
        )
        if capital is None:
            console.print(
                f"Capital input: {account_value:,.2f} USD (from {mode_label} account)"
            )
        else:
            console.print(f"Capital input: {capital:,.2f} {settings.capital_currency}")
            console.print(f"Capital used for sizing: {account_value:,.2f} USD")
        if not market_open:
            console.print(
                f"Market state: {market_state}; buy orders will use ETH session."
            )

        if not instructions:
            console.print("No paper orders were required.")
            return

        console.print(f"Submitting {mode_label} orders...")
        _submit_orders_with_duplicate_guard(
            trade_client, instructions, mode_label, render_order_response
        )
    finally:
        trade_client.close()
        quote_client.close()


@app.command()
def auto_run(
    symbols: str | None = typer.Option(
        None, help="Comma-separated US symbols to trade."
    ),
    benchmark_symbol: str | None = typer.Option(
        None, help="Benchmark symbol used for market data alignment."
    ),
    history_days: int = typer.Option(
        2200, min=60, help="Calendar lookback window for historical data."
    ),
    capital: float | None = typer.Option(
        None,
        min=5.0,
        help="Override input capital used for sizing the paper rebalance (JPY by default).",
    ),
    fx_jpy_per_usd: float | None = typer.Option(
        None, min=0.01, help="JPY per USD used to convert paper capital for sizing."
    ),
    minimum_order_value: float = typer.Option(
        5.0, min=0.0, help="Minimum order value used for fractional sizing."
    ),
    poll_seconds: int = typer.Option(
        900, min=60, help="Seconds to wait between monitoring cycles."
    ),
    max_consecutive_failures: int = typer.Option(
        5, min=1, help="Maximum consecutive failed cycles before auto-run stops."
    ),
) -> None:
    settings = get_settings()
    _require_paper_mode(settings, "auto-run")

    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    paper_capital_input = capital if capital is not None else settings.initial_capital
    resolved_fx_rate = (
        fx_jpy_per_usd if fx_jpy_per_usd is not None else settings.fx_jpy_per_usd
    )
    paper_capital_usd = convert_capital_to_usd(
        paper_capital_input, settings.capital_currency, resolved_fx_rate
    )
    strategy = _build_monthly_strategy(settings)
    quote_client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    trade_client = MoomooPaperTradeClient(
        host=settings.opend_host, port=settings.opend_port
    )
    risk_state = RiskState()

    console.print(
        f"Starting auto-run monitor for {', '.join(selected_symbols)} vs {benchmark_label}; "
        f"polling every {poll_seconds} seconds."
    )
    console.print(
        f"Capital input: {paper_capital_input:,.2f} {settings.capital_currency}"
    )
    console.print(f"Capital used for sizing: {paper_capital_usd:,.2f} USD")

    try:
        consecutive_failures = 0
        while True:
            try:
                price_frame, benchmark_series = quote_client.fetch_price_panel(
                    selected_symbols, benchmark_label, history_days=history_days
                )
                position_frame = trade_client.get_position_frame()
                current_positions = _position_quantities(position_frame)
                symbol_universe = list(
                    dict.fromkeys([*selected_symbols, *current_positions.keys()])
                )
                latest_prices = {
                    symbol: float(price_frame.iloc[-1][symbol])
                    for symbol in price_frame.columns
                }
                account_value = trade_client.get_account_value()
                market_state = _fetch_market_state(quote_client, benchmark_label)
                market_open = _is_regular_market_open(market_state)

                shock_reason = detect_market_shock(
                    benchmark_series, settings.market_shock_drop_pct
                )
                if shock_reason:
                    console.print(
                        f"{price_frame.index[-1].date()}: risk stop active - {shock_reason}"
                    )
                    sleep(poll_seconds)
                    continue

                drawdown_reason = update_drawdown_state(
                    account_value, risk_state, settings.max_drawdown_pct
                )
                if drawdown_reason:
                    liquidation_orders = build_liquidation_orders(
                        current_positions,
                        latest_prices,
                        drawdown_reason,
                        session=Session.NONE if market_open else Session.ETH,
                        fill_outside_rth=not market_open,
                    )
                    render_risk_orders(
                        liquidation_orders, current_positions, "Risk Stop Orders"
                    )
                    if liquidation_orders:
                        console.print("Submitting risk stop liquidation orders...")
                        _submit_orders_with_duplicate_guard(
                            trade_client,
                            liquidation_orders,
                            "paper",
                            render_order_response,
                        )
                    console.print(
                        f"{price_frame.index[-1].date()}: trading halted - {drawdown_reason}"
                    )
                    break

                decision = strategy.decide(price_frame, price_frame.index[-1])
                plan = build_paper_plan(
                    price_frame,
                    decision,
                    paper_capital_usd,
                    minimum_order_value=minimum_order_value,
                )
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
                    render_paper_trade_plan(
                        plan,
                        benchmark_label,
                        ", ".join(symbol_universe),
                        benchmark_series,
                        current_positions,
                        risk_orders,
                    )
                    console.print(
                        f"Capital input: {paper_capital_input:,.2f} {settings.capital_currency}"
                    )
                    console.print(f"Capital used for sizing: {plan.capital:,.2f} USD")
                    if not market_open:
                        console.print(
                            f"Market state: {market_state}; buy orders will use ETH session."
                        )
                    render_risk_orders(
                        risk_orders, current_positions, "Risk Exit Orders"
                    )
                    console.print("Submitting risk exit orders...")
                    _submit_orders_with_duplicate_guard(
                        trade_client, risk_orders, "paper", render_order_response
                    )
                    sleep(poll_seconds)
                    continue

                if instructions:
                    render_paper_trade_plan(
                        plan,
                        benchmark_label,
                        ", ".join(symbol_universe),
                        benchmark_series,
                        current_positions,
                        instructions,
                    )
                    console.print(
                        f"Capital input: {paper_capital_input:,.2f} {settings.capital_currency}"
                    )
                    console.print(f"Capital used for sizing: {plan.capital:,.2f} USD")
                    if not market_open:
                        console.print(
                            f"Market state: {market_state}; buy orders will use ETH session."
                        )
                    console.print("Submitting paper orders...")
                    _submit_orders_with_duplicate_guard(
                        trade_client, instructions, "paper", render_order_response
                    )
                else:
                    console.print(
                        f"{price_frame.index[-1].date()}: no rebalance required; monitoring only."
                    )

                consecutive_failures = 0
                sleep(poll_seconds)
            except Exception as exc:
                consecutive_failures += 1
                wait_seconds = min(
                    poll_seconds * (2 ** (consecutive_failures - 1)), poll_seconds * 8
                )
                console.print(
                    f"auto-run cycle failed ({consecutive_failures}/{max_consecutive_failures}): {exc}"
                )
                if consecutive_failures >= max_consecutive_failures:
                    console.print("auto-run stopped after repeated failures.")
                    break
                sleep(wait_seconds)
    finally:
        trade_client.close()
        quote_client.close()


if __name__ == "__main__":
    app()
