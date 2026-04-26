"""Research and analysis CLI commands module.

Purpose: Backtest, verify-api, research, satellite, and validate commands.
         Uses deferred imports from moomoo_bot.cli for monkeypatch compatibility.
Related: _cli_app.py, cli.py, research.py, backtest.py.
"""

from __future__ import annotations

from pathlib import Path

import typer

from moomoo_bot._cli_app import app

import moomoo_bot.cli as _cli


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
    from moomoo_bot.backtest import make_demo_prices
    from moomoo_bot.cli_helpers import (
        parse_symbols as _parse_symbols,
        load_benchmark_series as _load_benchmark_series,
        load_price_frame as _load_price_frame,
    )
    from moomoo_bot.cli_render import (
        render_backtest_result,
        render_satellite_results,
    )
    from moomoo_bot.research import (
        default_momentum_search_configs,
        default_satellite_weights,
        search_satellite_candidates,
    )

    settings = _cli.get_settings()
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
        if resolved_satellite_weight >= 0
        else default_satellite_weights()
    )
    candidates = search_satellite_candidates(
        price_frame,
        benchmark_series,
        configs=candidate_configs,
        satellite_weights=candidate_weights,
        transaction_cost_per_trade=settings.transaction_cost_per_trade,
        transaction_cost_bps=settings.transaction_cost_bps,
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
    from moomoo_bot.backtest import run_backtest
    from moomoo_bot.broker import MoomooOpenDClient
    from moomoo_bot.cli_helpers import (
        build_monthly_strategy as _build_monthly_strategy,
        parse_symbols as _parse_symbols,
        requires_benchmark_prices as _requires_benchmark_prices,
    )
    from moomoo_bot.cli_render import (
        render_backtest_result,
        render_snapshot,
    )

    settings = _cli.get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol

    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        strategy = _build_monthly_strategy(settings)
        snapshot = client.fetch_market_snapshot([*selected_symbols, benchmark_label])
        price_frame, benchmark_series = client.fetch_price_panel(
            selected_symbols,
            benchmark_label,
            history_days=history_days,
            include_benchmark_in_prices=_requires_benchmark_prices(strategy),
        )
        if len(price_frame) < settings.warmup_window:
            raise typer.BadParameter(
                f"Only {len(price_frame)} aligned rows were returned; need at least {settings.warmup_window}."
            )

        result = run_backtest(
            price_frame,
            benchmark_series,
            strategy,
            transaction_cost_per_trade=settings.transaction_cost_per_trade,
            transaction_cost_bps=settings.transaction_cost_bps,
        )
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
    from moomoo_bot.broker import MoomooOpenDClient
    from moomoo_bot.cli_helpers import parse_symbols as _parse_symbols
    from moomoo_bot.cli_render import render_research_results
    from moomoo_bot.research import (
        default_momentum_search_configs,
        search_momentum_candidates,
    )

    settings = _cli.get_settings()
    selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    configs = default_momentum_search_configs()

    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        price_frame, benchmark_series = client.fetch_price_panel(
            selected_symbols, benchmark_label, history_days=history_days
        )
        results = search_momentum_candidates(
            price_frame,
            benchmark_series,
            configs=configs,
            split_ratio=split_ratio,
            transaction_cost_per_trade=settings.transaction_cost_per_trade,
            transaction_cost_bps=settings.transaction_cost_bps,
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
    from moomoo_bot.broker import MoomooOpenDClient
    from moomoo_bot.cli_helpers import (
        parse_symbols as _parse_symbols,
        parse_weights as _parse_weights,
    )
    from moomoo_bot.cli_render import render_satellite_results
    from moomoo_bot.research import (
        default_momentum_search_configs,
        default_satellite_weights,
        search_satellite_candidates,
    )

    settings = _cli.get_settings()
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
            transaction_cost_per_trade=settings.transaction_cost_per_trade,
            transaction_cost_bps=settings.transaction_cost_bps,
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
def validate(
    prices_csv: Path | None = typer.Option(
        None, help="CSV with date index and symbol columns for stress analysis."
    ),
    benchmark_csv: Path | None = typer.Option(
        None, help="Optional CSV file with a benchmark series."
    ),
    symbols: str | None = typer.Option(
        None, help="Comma-separated symbols; used when prices_csv is omitted."
    ),
    benchmark_symbol: str | None = typer.Option(
        None, help="Benchmark label used in reports."
    ),
    periods: int = typer.Option(
        1008, min=252, help="Number of trading days for demo data."
    ),
    seed: int = typer.Option(7, min=0, help="Random seed for demo data."),
    base_cost_bps: float = typer.Option(
        None, min=0.0, help="Base transaction cost in bps. Defaults to config value."
    ),
    walk_forward: bool = typer.Option(True, help="Also run walk-forward analysis."),
    train_days: int = typer.Option(
        504, min=126, help="Walk-forward training window in trading days."
    ),
    test_days: int = typer.Option(
        126, min=21, help="Walk-forward test window in trading days."
    ),
) -> None:
    """Validate strategy robustness via cost stress analysis and walk-forward backtest."""
    from moomoo_bot.backtest import (
        make_demo_prices,
        run_cost_stress_analysis,
        run_walk_forward_backtest,
    )
    from moomoo_bot.cli_helpers import (
        build_monthly_strategy as _build_monthly_strategy,
        load_benchmark_series as _load_benchmark_series,
        load_price_frame as _load_price_frame,
    )
    from moomoo_bot.cli_render import (
        console,
        render_cost_stress_result,
        render_walk_forward_result,
    )

    settings = _cli.get_settings()
    selected_symbols = _cli._parse_symbols(symbols) if symbols else settings.symbol_list
    if symbols:
        from moomoo_bot.cli_helpers import parse_symbols as _parse_symbols

        selected_symbols = _parse_symbols(symbols) or settings.symbol_list
    benchmark_label = benchmark_symbol or settings.benchmark_symbol
    resolved_base_bps = (
        base_cost_bps if base_cost_bps is not None else settings.transaction_cost_bps
    )

    if prices_csv is None:
        price_frame, benchmark_series = make_demo_prices(
            selected_symbols, periods=periods, seed=seed
        )
    else:
        from moomoo_bot.cli_helpers import (
            load_price_frame as _load_price_frame,
            load_benchmark_series as _load_benchmark_series,
        )

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

    def _make_strategy():
        return _build_monthly_strategy(
            price_frame.columns.tolist(),
            benchmark_label,
            satellite_weight=settings.backtest_satellite_weight,
        )

    console.print("[bold]Running cost stress analysis…[/bold]")
    stress_results = run_cost_stress_analysis(
        price_frame,
        benchmark_series,
        strategy_factory=_make_strategy,
        base_bps=resolved_base_bps,
    )
    render_cost_stress_result(stress_results)

    if walk_forward:
        required_rows = train_days + test_days
        if len(price_frame) < required_rows:
            console.print(
                f"[yellow]Skipping walk-forward: need {required_rows} rows, "
                f"have {len(price_frame)}.[/yellow]"
            )
        else:
            console.print("[bold]Running walk-forward backtest…[/bold]")
            wf_result = run_walk_forward_backtest(
                price_frame,
                benchmark_series,
                strategy_factory=_make_strategy,
                train_period_days=train_days,
                test_period_days=test_days,
                transaction_cost_bps=resolved_base_bps,
            )
            render_walk_forward_result(wf_result)
