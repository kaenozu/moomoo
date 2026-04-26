from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest
from moomoo import TrdEnv
import typer

from moomoo_bot import cli
from moomoo_bot import orchestrator
from moomoo_bot.cli_helpers import build_monthly_strategy
from moomoo_bot.config import Settings
from moomoo_bot.state import (
    ExecutionAuditSummary,
    ExecutionFillRecord,
    OrderRecord,
    TaxLotRealizationRecord,
)
from moomoo_bot.strategy.base import TradeDecision
from moomoo_bot.strategy.momentum import CoreSatelliteStrategy


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
    assert calls[0]["max_position_weight"] == settings.live_max_position_weight


def test_autopilot_delegates_to_orchestrator_with_env_defaults(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL,US.MSFT",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        initial_capital=120000.0,
        fx_jpy_per_usd=150.0,
        autopilot_history_days=2000,
        autopilot_poll_seconds=600,
        autopilot_max_consecutive_failures=7,
        autopilot_minimum_order_value=9.0,
    )
    calls: list[dict] = []

    def fake_run_auto_monitor(**kwargs):
        calls.append(kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(orchestrator, "run_auto_monitor", fake_run_auto_monitor)

    with pytest.raises(KeyboardInterrupt):
        cli.autopilot()

    assert len(calls) == 1
    assert calls[0]["settings"] == settings
    assert calls[0]["symbols"] == ["US.AAPL", "US.MSFT"]
    assert calls[0]["benchmark_symbol"] == "US.VT"
    assert calls[0]["history_days"] == 2000
    assert calls[0]["capital"] == 120000.0
    assert calls[0]["fx_jpy_per_usd"] == 150.0
    assert calls[0]["minimum_order_value"] == 9.0
    assert calls[0]["poll_seconds"] == 600
    assert calls[0]["max_consecutive_failures"] == 7
    assert calls[0]["max_position_weight"] == settings.live_max_position_weight


def test_autopilot_requires_paper_mode(monkeypatch) -> None:
    settings = Settings(symbols="US.AAPL", benchmark_symbol="US.VT", execution_mode="live")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    with pytest.raises(typer.BadParameter, match="requires MOOMOO_BOT_EXECUTION_MODE=paper"):
        cli.autopilot()


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
    assert calls[0]["max_position_weight"] == settings.live_max_position_weight


def test_paper_repair_delegates_to_orchestrator(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
    )
    calls: list[dict] = []

    def fake_run_paper_repair(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(orchestrator, "run_paper_repair", fake_run_paper_repair)

    cli.paper_repair(benchmark_symbol=None, clear_local_state=True)

    assert len(calls) == 1
    assert calls[0]["settings"] == settings
    assert calls[0]["benchmark_symbol"] == "US.VT"
    assert calls[0]["clear_local_state"] is True


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
    assert calls[0]["max_position_weight"] == settings.live_max_position_weight


def test_build_monthly_strategy_uses_core_satellite_wrapper() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
    )

    strategy = build_monthly_strategy(settings)

    assert isinstance(strategy, CoreSatelliteStrategy)
    assert strategy.benchmark_symbol == "US.VT"
    assert strategy.satellite_weight == settings.satellite_weight


def test_execution_report_loads_state_and_renders_audit(monkeypatch) -> None:
    captured: dict[str, object] = {}
    settings = Settings(execution_mode="live")

    class FakeStateStore:
        def __init__(self, db_path=None, execution_mode=None):
            captured["db_path"] = db_path
            captured["execution_mode"] = execution_mode

        def summarize_execution_activity(self, symbol=None):
            captured["summary_symbol"] = symbol
            return ExecutionAuditSummary(
                order_count=3,
                pending_order_count=1,
                fill_count=2,
                total_fees=1.6,
                total_slippage=10.0,
                realized_pnl=26.1,
            )

        def get_execution_fills(self, symbol=None, limit=None):
            captured["fills_symbol"] = symbol
            captured["fills_limit"] = limit
            return [
                ExecutionFillRecord(
                    order_id="fill-1",
                    symbol="US.AAPL",
                    side="BUY",
                    fill_quantity=1.0,
                    intended_price=100.0,
                    fill_price=101.0,
                    fee_amount=0.1,
                    slippage_amount=1.0,
                    filled_at="2025-01-03T15:30:00+00:00",
                )
            ]

        def get_tax_lot_realizations(self, symbol=None, limit=None):
            captured["realizations_symbol"] = symbol
            captured["realizations_limit"] = limit
            return [
                TaxLotRealizationRecord(
                    symbol="US.AAPL",
                    sell_order_id="sell-1",
                    quantity=1.0,
                    realized_pnl=2.5,
                    closed_at="2025-01-03T15:31:00+00:00",
                )
            ]

        def load_recent_orders(self, limit=50):
            captured["orders_limit"] = limit
            return [
                OrderRecord(
                    order_id="pending-1",
                    symbol="US.AAPL",
                    side="BUY",
                    quantity=1.0,
                    price=99.0,
                    status="submitted",
                )
            ]

        def close(self) -> None:
            captured["closed"] = True

    def fake_render(summary, recent_fills, recent_realizations, recent_orders, symbol_label):
        captured["rendered"] = {
            "summary": summary,
            "recent_fills": recent_fills,
            "recent_realizations": recent_realizations,
            "recent_orders": recent_orders,
            "symbol_label": symbol_label,
        }

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "StateStore", FakeStateStore)
    monkeypatch.setattr(cli, "render_execution_report", fake_render)

    cli.execution_report(
        symbol="US.AAPL",
        fills_limit=5,
        realizations_limit=4,
        orders_limit=3,
        db_path=None,
    )

    assert captured["execution_mode"] == "live"
    assert captured["summary_symbol"] == "US.AAPL"
    assert captured["fills_symbol"] == "US.AAPL"
    assert captured["fills_limit"] == 5
    assert captured["realizations_symbol"] == "US.AAPL"
    assert captured["realizations_limit"] == 4
    assert captured["orders_limit"] == 3
    assert captured["closed"] is True
    assert captured["rendered"]["symbol_label"] == "US.AAPL"
