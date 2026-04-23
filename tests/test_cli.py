from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest
from moomoo import TrdEnv
import typer

from moomoo_bot import cli
from moomoo_bot import orchestrator
from moomoo_bot.config import Settings
from moomoo_bot.strategy.base import TradeDecision


def test_auto_run_delegates_to_orchestrator(monkeypatch) -> None:
    settings = Settings(symbols="US.AAPL,US.MSFT", benchmark_symbol="US.VT", execution_mode="paper")
    calls: list[dict] = []

    def fake_run_auto_monitor(**kwargs):
        calls.append(kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(orchestrator, "run_auto_monitor", fake_run_auto_monitor)

    with pytest.raises(KeyboardInterrupt):
        cli.auto_run(
            symbols=None,
            benchmark_symbol=None,
            history_days=2200,
            capital=None,
            fx_jpy_per_usd=None,
            minimum_order_value=5.0,
            poll_seconds=1,
            max_consecutive_failures=5,
        )

    assert len(calls) == 1
    assert calls[0]["settings"] == settings
    assert calls[0]["symbols"] == ["US.AAPL", "US.MSFT"]
    assert calls[0]["benchmark_symbol"] == "US.VT"


def test_live_trade_requires_explicit_confirmation(monkeypatch) -> None:
    settings = Settings(symbols="US.AAPL", benchmark_symbol="US.VT", execution_mode="live", allow_live_trading=True)

    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    with pytest.raises(typer.BadParameter, match="--confirm-live-trading"):
        cli.live_trade(
            symbols=None,
            benchmark_symbol=None,
            history_days=2200,
            capital=None,
            fx_jpy_per_usd=None,
            minimum_order_value=5.0,
            confirm_live_trading=False,
        )


def test_live_trade_delegates_to_orchestrator_with_real_env(monkeypatch) -> None:
    settings = Settings(symbols="US.AAPL", benchmark_symbol="US.VT", execution_mode="live", allow_live_trading=True)
    calls: list[dict] = []

    def fake_run_one_shot_trade(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(orchestrator, "run_one_shot_trade", fake_run_one_shot_trade)

    cli.live_trade(
        symbols=None,
        benchmark_symbol=None,
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        confirm_live_trading=True,
    )

    assert len(calls) == 1
    assert calls[0]["trade_env"] == TrdEnv.REAL
    assert calls[0]["max_position_weight"] == settings.live_max_position_weight


def test_paper_trade_delegates_to_orchestrator(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=102_000.0,
    )
    calls: list[dict] = []

    def fake_run_one_shot_trade(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(orchestrator, "run_one_shot_trade", fake_run_one_shot_trade)

    cli.paper_trade(
        symbols=None,
        benchmark_symbol=None,
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
    )

    assert len(calls) == 1
    assert calls[0]["trade_env"] == TrdEnv.SIMULATE


def test_paper_run_delegates_to_orchestrator_with_submit_orders_false(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    calls: list[dict] = []

    def fake_run_one_shot_trade(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(orchestrator, "run_one_shot_trade", fake_run_one_shot_trade)

    cli.paper_run(
        symbols=None,
        benchmark_symbol=None,
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
    )

    assert len(calls) == 1
    assert calls[0]["trade_env"] == TrdEnv.SIMULATE
    assert calls[0]["submit_orders"] is False
