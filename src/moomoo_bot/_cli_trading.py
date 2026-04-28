"""Trading CLI commands module.

Purpose: Paper/live trading, auto-monitor, and autopilot commands.
         Uses deferred imports from moomoo_bot.cli for monkeypatch compatibility.
Related: _cli_app.py, cli.py, orchestrator/.
"""

from __future__ import annotations

import typer
from moomoo import TrdEnv

from moomoo_bot._cli_app import app, cli_module
from moomoo_bot import orchestrator


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
        help="Override input capital used for sizing the paper rebalance (JPY by default).",
    ),
    fx_jpy_per_usd: float | None = typer.Option(
        None, min=0.01, help="JPY per USD used to convert paper capital for sizing."
    ),
    minimum_order_value: float = typer.Option(
        5.0, min=0.0, help="Minimum order value used for fractional sizing."
    ),
) -> None:
    from moomoo_bot.cli_helpers import parse_symbols as _parse_symbols

    _cli = cli_module()
    settings = _cli.get_settings()
    _require_paper_mode(settings, "paper-run")
    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=_parse_symbols(symbols) or settings.symbol_list,
        benchmark_symbol=benchmark_symbol or settings.benchmark_symbol,
        history_days=history_days,
        capital=capital,
        fx_jpy_per_usd=fx_jpy_per_usd,
        minimum_order_value=minimum_order_value,
        max_position_weight=settings.live_max_position_weight,
        submit_orders=False,
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
    local_sim: bool = typer.Option(
        False,
        "--local-sim",
        help="Use local PaperSimulator only; skip Moomoo API paper account.",
    ),
) -> None:
    from moomoo_bot.cli_helpers import parse_symbols as _parse_symbols

    _cli = cli_module()
    settings = _cli.get_settings()
    _require_paper_mode(settings, "paper-trade")
    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=_parse_symbols(symbols) or settings.symbol_list,
        benchmark_symbol=benchmark_symbol or settings.benchmark_symbol,
        history_days=history_days,
        capital=capital,
        fx_jpy_per_usd=fx_jpy_per_usd,
        minimum_order_value=minimum_order_value,
        max_position_weight=settings.live_max_position_weight,
        use_local_sim=local_sim,
    )


@app.command()
def paper_repair(
    benchmark_symbol: str | None = typer.Option(
        None, help="Benchmark symbol used to infer market hours for repair orders."
    ),
    clear_local_state: bool = typer.Option(
        True,
        "--clear-local-state/--keep-local-state",
        help="Delete the local paper state DB after repair.",
    ),
) -> None:
    _cli = cli_module()
    settings = _cli.get_settings()
    _require_paper_mode(settings, "paper-repair")
    orchestrator.run_paper_repair(
        settings=settings,
        benchmark_symbol=benchmark_symbol or settings.benchmark_symbol,
        clear_local_state=clear_local_state,
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
    from moomoo_bot.cli_helpers import parse_symbols as _parse_symbols

    _cli = cli_module()
    settings = _cli.get_settings()
    _require_live_mode(settings, "live-trade", confirm_live_trading)
    orchestrator.run_one_shot_trade(
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
    from moomoo_bot.cli_helpers import parse_symbols as _parse_symbols

    _cli = cli_module()
    settings = _cli.get_settings()
    _require_paper_mode(settings, "auto-run")
    orchestrator.run_auto_monitor(
        settings=settings,
        symbols=_parse_symbols(symbols) or settings.symbol_list,
        benchmark_symbol=benchmark_symbol or settings.benchmark_symbol,
        history_days=history_days,
        capital=capital,
        fx_jpy_per_usd=fx_jpy_per_usd,
        minimum_order_value=minimum_order_value,
        poll_seconds=poll_seconds,
        max_consecutive_failures=max_consecutive_failures,
        max_position_weight=settings.live_max_position_weight,
    )


@app.command()
def autopilot() -> None:
    """Start fully automated paper-trading loop using .env defaults only."""
    from moomoo_bot.cli_render import console

    _cli = cli_module()
    settings = _cli.get_settings()
    _require_paper_mode(settings, "autopilot")
    console.print("[bold green]Autopilot mode started[/bold green]")
    console.print(
        "Using .env defaults: symbols, benchmark, risk limits, webhook, health check."
    )
    orchestrator.run_auto_monitor(
        settings=settings,
        symbols=settings.symbol_list,
        benchmark_symbol=settings.benchmark_symbol,
        history_days=settings.autopilot_history_days,
        capital=settings.initial_capital,
        fx_jpy_per_usd=settings.fx_jpy_per_usd,
        minimum_order_value=settings.autopilot_minimum_order_value,
        poll_seconds=settings.autopilot_poll_seconds,
        max_consecutive_failures=settings.autopilot_max_consecutive_failures,
        max_position_weight=settings.live_max_position_weight,
    )
