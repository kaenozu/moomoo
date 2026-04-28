from __future__ import annotations

from dataclasses import dataclass, field
import json

import pandas as pd
import pytest

from moomoo import TrdEnv, TrdSide

from moomoo_bot import orchestrator
from moomoo_bot.orchestrator import cycle as orchestrator_cycle
from moomoo_bot.config import Settings
from moomoo_bot.paper import PaperPlan
from moomoo_bot.state import EquitySnapshot, OrderRecord, PersistentRiskState, StateStore
from moomoo_bot.strategy.base import TradeDecision


def test_run_one_shot_trade_uses_injected_clients_and_strategy() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    strategy = FakeStrategy()
    state_store = FakeStateStore()

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=strategy,
        state_store=state_store,
    )

    assert strategy.decide_calls == 1
    assert quote_client.fetch_price_panel_calls == 1
    assert trade_client.submit_order_calls == 1


def test_run_one_shot_trade_uses_settings_execution_mode_for_default_state_store(
    monkeypatch,
) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="live",
        allow_live_trading=True,
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    strategy = FakeStrategy()
    captured: dict[str, object] = {}

    class CapturingStateStore(FakeStateStore):
        def __init__(self, db_path=None, execution_mode=None):
            captured["db_path"] = db_path
            captured["execution_mode"] = execution_mode
            super().__init__()

    monkeypatch.setattr(orchestrator, "StateStore", CapturingStateStore)

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.REAL,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=strategy,
    )

    assert captured["db_path"] is None
    assert captured["execution_mode"] == "live"


def test_run_one_shot_trade_records_orders_and_reconciles_pending_orders() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient(
        order_frame=pd.DataFrame(
            {
                "order_id": ["pending-1"],
                "order_status": ["FILLED_ALL"],
                "code": ["US.MSFT"],
                "trd_side": ["BUY"],
                "qty": [2.0],
                "price": [250.0],
                "filled_quantity": [2.0],
                "dealt_avg_price": [251.25],
                "commission": [1.75],
                "updated_time": ["2025-01-03T14:30:00+00:00"],
                "remark": ["pending-test"],
            }
        )
    )
    state_store = FakeStateStore(
        pending_orders=[
            OrderRecord(
                order_id="pending-1",
                symbol="US.MSFT",
                side="BUY",
                quantity=2.0,
                price=250.0,
                status="submitted",
                reason="pending-test",
            )
        ]
    )
    strategy = FakeStrategy()

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=strategy,
        state_store=state_store,
    )

    assert trade_client.get_order_frame_calls == 1
    assert state_store.order_status_updates == [("pending-1", "filled_all", 2.0)]
    assert state_store.order_status_update_details == [
        {
            "fill_price": 251.25,
            "broker_accepted_price": 250.0,
            "fee_amount": 1.75,
            "filled_at": "2025-01-03T14:30:00+00:00",
        }
    ]
    assert state_store.recorded_orders[0].order_id == "1"
    assert state_store.recorded_orders[0].symbol == "US.AAPL"


def test_run_one_shot_trade_uses_snapshot_price_for_submitted_orders() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    strategy = FakeStrategy()
    state_store = FakeStateStore()

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=strategy,
        state_store=state_store,
    )

    assert quote_client.fetch_market_snapshot_calls == 1
    assert trade_client.last_instruction_price == 105.56


def test_run_one_shot_trade_requests_tradable_benchmark_prices_when_needed() -> None:
    class BenchmarkAwareStrategy:
        requires_benchmark_prices = True

        def decide(self, prices: pd.DataFrame, as_of: pd.Timestamp) -> TradeDecision:
            return TradeDecision(
                as_of=as_of,
                target_weights={"US.VT": 1.0},
                reason="benchmark_only",
            )

    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    state_store = FakeStateStore()

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=BenchmarkAwareStrategy(),
        state_store=state_store,
    )

    assert quote_client.last_include_benchmark_in_prices is True
    assert trade_client.submit_order_calls == 1


def test_run_one_shot_trade_forwards_explicit_position_cap(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    strategy = FakeStrategy()
    captured: dict[str, float] = {}

    def fake_build_paper_plan(
        prices,
        decision,
        capital,
        minimum_order_value=5.0,
        max_position_weight=1.0,
        fractional_share_precision=1000.0,
    ):
        captured["max_position_weight"] = max_position_weight
        captured["fractional_share_precision"] = fractional_share_precision
        return PaperPlan(
            as_of=decision.as_of,
            capital=capital,
            reason=decision.reason,
            allocations=[],
            cash_remaining=capital,
        )

    monkeypatch.setattr(orchestrator, "build_paper_plan", fake_build_paper_plan)
    monkeypatch.setattr(orchestrator, "render_paper_trade_plan", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "render_risk_orders", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_submit_orders_with_duplicate_guard", lambda *args, **kwargs: None)

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        max_position_weight=0.35,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=strategy,
        state_store=FakeStateStore(),
    )

    assert captured["max_position_weight"] == 0.35
    assert captured["fractional_share_precision"] == settings.fractional_share_precision



def test_run_paper_repair_covers_short_positions(monkeypatch, tmp_path) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        state_db_path=tmp_path / "paper-state.db",
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient(
        position_frame=pd.DataFrame(
            {
                "code": ["US.AVGO"],
                "qty": [-4010.0],
                "position_side": ["SHORT"],
            }
        )
    )
    state_store = FakeStateStore()

    result = orchestrator.run_paper_repair(
        settings=settings,
        benchmark_symbol="US.VT",
        quote_client=quote_client,
        trade_client=trade_client,
        state_store=state_store,
        clear_local_state=False,
    )

    assert result is True
    assert trade_client.submit_order_calls == 1
    assert trade_client.submitted_instructions[0].side == TrdSide.BUY
    assert trade_client.submitted_instructions[0].symbol == "US.AVGO"
    assert trade_client.submitted_instructions[0].quantity == 4010.0
    assert state_store.saved_states == []


def test_run_paper_repair_prefers_long_position_side_over_negative_qty(tmp_path) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        state_db_path=tmp_path / "paper-state.db",
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient(
        position_frame=pd.DataFrame(
            {
                "code": ["US.AVGO"],
                "qty": [-4010.0],
                "position_side": ["LONG"],
            }
        )
    )
    state_store = FakeStateStore()

    result = orchestrator.run_paper_repair(
        settings=settings,
        benchmark_symbol="US.VT",
        quote_client=quote_client,
        trade_client=trade_client,
        state_store=state_store,
        clear_local_state=False,
    )

    assert result is True
    assert trade_client.submit_order_calls == 1
    assert trade_client.submitted_instructions[0].side == TrdSide.SELL
    assert trade_client.submitted_instructions[0].symbol == "US.AVGO"
    assert trade_client.submitted_instructions[0].quantity == 4010.0


def test_run_one_shot_trade_routes_zero_buying_power_to_repair(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient(buying_power=0.0)
    strategy = FakeStrategy()
    captured: dict[str, object] = {}

    def fake_run_paper_repair(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(orchestrator, "run_paper_repair", fake_run_paper_repair)

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=strategy,
        state_store=FakeStateStore(),
    )

    assert captured["settings"] == settings
    assert captured["benchmark_symbol"] == "US.VT"
    assert captured["clear_local_state"] is True


def test_run_one_shot_trade_zero_buying_power_falls_back_to_preview(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient(buying_power=0.0)
    strategy = FakeStrategy()

    def fake_run_paper_repair(**_kwargs):
        return True

    monkeypatch.setattr(orchestrator, "run_paper_repair", fake_run_paper_repair)

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=1000.0,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=strategy,
        state_store=FakeStateStore(),
    )

    assert strategy.decide_calls == 1
    assert trade_client.submit_order_calls == 0


def test_run_one_shot_trade_caps_paper_capital_to_buying_power(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="JPY",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient(account_value=666.67, buying_power=200.0)
    strategy = FakeStrategy()
    captured: dict[str, float] = {}

    def fake_build_paper_plan(
        prices,
        decision,
        capital,
        minimum_order_value=5.0,
        max_position_weight=1.0,
        fractional_share_precision=1000.0,
    ):
        captured["capital"] = capital
        return PaperPlan(
            as_of=decision.as_of,
            capital=capital,
            reason=decision.reason,
            allocations=[],
            cash_remaining=capital,
        )

    monkeypatch.setattr(orchestrator, "build_paper_plan", fake_build_paper_plan)
    monkeypatch.setattr(orchestrator, "render_paper_trade_plan", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "render_risk_orders", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_submit_orders_with_duplicate_guard", lambda *args, **kwargs: None)

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=100_000.0,
        fx_jpy_per_usd=150.0,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=strategy,
        state_store=FakeStateStore(),
    )

    assert captured["capital"] == 200.0



def test_run_one_shot_trade_halves_position_cap_at_drawdown_tier_one(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        max_drawdown_pct=0.15,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient(account_value=108_000.0)
    state_store = FakeStateStore(
        risk_state=PersistentRiskState(
            peak_account_value=120_000.0,
            halted=False,
            halted_reason=None,
            drawdown_tier=0,
            daily_order_date="2025-01-02",
            last_equity_value=118_000.0,
        )
    )
    strategy = FakeStrategy()
    captured: dict[str, float] = {}

    def fake_build_paper_plan(
        prices,
        decision,
        capital,
        minimum_order_value=5.0,
        max_position_weight=1.0,
        fractional_share_precision=1000.0,
    ):
        captured["max_position_weight"] = max_position_weight
        return PaperPlan(
            as_of=decision.as_of,
            capital=capital,
            reason=decision.reason,
            allocations=[],
            cash_remaining=capital,
        )

    monkeypatch.setattr(orchestrator, "build_paper_plan", fake_build_paper_plan)
    monkeypatch.setattr(orchestrator, "render_paper_trade_plan", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "render_risk_orders", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_submit_orders_with_duplicate_guard", lambda *args, **kwargs: None)

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        max_position_weight=0.4,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=strategy,
        state_store=state_store,
    )

    assert captured["max_position_weight"] == 0.2


def test_run_one_shot_trade_aborts_when_kill_switch_is_active(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()

    monkeypatch.setattr(orchestrator, "_is_kill_switch_active", lambda: True)

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
    )

    assert quote_client.fetch_price_panel_calls == 0
    assert trade_client.submit_order_calls == 0


def test_run_one_shot_trade_restores_persisted_drawdown_state() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        max_drawdown_pct=0.15,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient(
        account_value=100_000.0,
        position_frame=pd.DataFrame({"code": ["US.AAPL"], "qty": [1.0]}),
    )
    state_store = FakeStateStore(
        risk_state=PersistentRiskState(
            peak_account_value=120_000.0,
            halted=False,
            halted_reason=None,
            drawdown_tier=1,
            daily_order_date="2025-01-02",
            last_equity_value=118_000.0,
        )
    )

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        state_store=state_store,
    )

    assert trade_client.submit_order_calls == 1
    assert state_store.saved_states[-1].halted is True
    assert state_store.saved_states[-1].peak_account_value == 120_000.0
    assert "max_drawdown" in (state_store.saved_states[-1].halted_reason or "")


def test_run_one_shot_trade_respects_daily_order_cap() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        max_daily_orders=1,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    state_store = FakeStateStore(
        risk_state=PersistentRiskState(
            daily_order_count=1,
            daily_order_date="2025-01-03",
        )
    )

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        state_store=state_store,
    )

    assert trade_client.submit_order_calls == 0
    assert state_store.saved_states[-1].daily_order_count == 1


def test_run_one_shot_trade_persists_daily_order_count_after_submit() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        max_daily_orders=5,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    state_store = FakeStateStore(
        risk_state=PersistentRiskState(
            daily_order_count=1,
            daily_order_date="2025-01-03",
        )
    )

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        trade_client=trade_client,
        state_store=state_store,
    )

    assert trade_client.submit_order_calls == 1
    assert state_store.saved_states[-1].daily_order_count == 2
    assert state_store.saved_states[-1].daily_order_date == "2025-01-03"


def test_run_auto_monitor_uses_injected_sleep_and_clients() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    strategy = FakeStrategy()
    state_store = FakeStateStore()
    sleep_calls: list[int] = []

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        raise KeyboardInterrupt

    try:
        orchestrator.run_auto_monitor(
            settings=settings,
            symbols=["US.AAPL"],
            benchmark_symbol="US.VT",
            history_days=2200,
            capital=None,
            fx_jpy_per_usd=None,
            minimum_order_value=5.0,
            poll_seconds=1,
            max_consecutive_failures=5,
            quote_client=quote_client,
            trade_client=trade_client,
            strategy=strategy,
            sleep_fn=fake_sleep,
            state_store=state_store,
        )
    except KeyboardInterrupt:
        pass

    assert strategy.decide_calls == 1
    assert quote_client.fetch_price_panel_calls == 1
    assert trade_client.submit_order_calls == 1
    assert sleep_calls == [1]


def test_run_auto_monitor_cleans_equity_history_and_reconciles_pending_orders() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        equity_retention_days=30,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient(
        order_frame=pd.DataFrame(
            {
                "order_id": ["pending-1"],
                "order_status": ["FILLED_ALL"],
                "code": ["US.MSFT"],
                "trd_side": ["BUY"],
                "qty": [2.0],
                "price": [250.0],
                "filled_quantity": [2.0],
                "remark": ["pending-test"],
            }
        )
    )
    state_store = FakeStateStore(
        pending_orders=[
            OrderRecord(
                order_id="pending-1",
                symbol="US.MSFT",
                side="BUY",
                quantity=2.0,
                price=250.0,
                status="submitted",
                reason="pending-test",
            )
        ]
    )
    strategy = FakeStrategy()
    sleep_calls: list[int] = []

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        raise KeyboardInterrupt

    try:
        orchestrator.run_auto_monitor(
            settings=settings,
            symbols=["US.AAPL"],
            benchmark_symbol="US.VT",
            history_days=2200,
            capital=None,
            fx_jpy_per_usd=None,
            minimum_order_value=5.0,
            poll_seconds=1,
            max_consecutive_failures=5,
            quote_client=quote_client,
            trade_client=trade_client,
            strategy=strategy,
            sleep_fn=fake_sleep,
            state_store=state_store,
        )
    except KeyboardInterrupt:
        pass

    assert state_store.order_status_updates == [("pending-1", "filled_all", 2.0)]
    assert state_store.cleanup_calls == [30]
    assert trade_client.get_order_frame_calls == 1
    assert sleep_calls == [1]


def test_run_auto_monitor_stops_when_kill_switch_is_active(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()

    monkeypatch.setattr(orchestrator, "_is_kill_switch_active", lambda: True)

    orchestrator.run_auto_monitor(
        settings=settings,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        poll_seconds=1,
        max_consecutive_failures=5,
        quote_client=quote_client,
        trade_client=trade_client,
    )

    assert quote_client.fetch_price_panel_calls == 0
    assert trade_client.submit_order_calls == 0


def test_run_auto_monitor_triggers_daily_loss_limit_from_persisted_equity() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        daily_loss_limit_pct=0.05,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient(
        account_value=94_000.0,
        position_frame=pd.DataFrame({"code": ["US.AAPL"], "qty": [2.0]}),
    )
    state_store = FakeStateStore(
        previous_equity=EquitySnapshot(
            timestamp="2025-01-02T21:00:00+00:00",
            account_value=100_000.0,
            cash=0.0,
            positions_json="{}",
            market_date="2025-01-02",
        )
    )
    sleep_calls: list[int] = []

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        raise KeyboardInterrupt

    try:
        orchestrator.run_auto_monitor(
            settings=settings,
            symbols=["US.AAPL"],
            benchmark_symbol="US.VT",
            history_days=2200,
            capital=None,
            fx_jpy_per_usd=None,
            minimum_order_value=5.0,
            poll_seconds=1,
            max_consecutive_failures=5,
            quote_client=quote_client,
            trade_client=trade_client,
            state_store=state_store,
            sleep_fn=fake_sleep,
        )
    except KeyboardInterrupt:
        pass

    assert trade_client.submit_order_calls == 1
    assert sleep_calls == [1]
    assert state_store.recorded_equity[0]["market_date"] == "2025-01-03"
    assert state_store.saved_states[-1].halted is True
    assert "daily_loss_limit" in (state_store.saved_states[-1].halted_reason or "")


def test_run_auto_monitor_skips_submissions_after_daily_order_cap() -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        max_daily_orders=1,
    )
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    state_store = FakeStateStore(
        risk_state=PersistentRiskState(
            daily_order_count=1,
            daily_order_date="2025-01-03",
        )
    )
    sleep_calls: list[int] = []

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        raise KeyboardInterrupt

    try:
        orchestrator.run_auto_monitor(
            settings=settings,
            symbols=["US.AAPL"],
            benchmark_symbol="US.VT",
            history_days=2200,
            capital=None,
            fx_jpy_per_usd=None,
            minimum_order_value=5.0,
            poll_seconds=1,
            max_consecutive_failures=5,
            quote_client=quote_client,
            trade_client=trade_client,
            state_store=state_store,
            sleep_fn=fake_sleep,
        )
    except KeyboardInterrupt:
        pass

    assert trade_client.submit_order_calls == 0
    assert sleep_calls == [1]
    assert state_store.saved_states[-1].daily_order_count == 1


def test_run_one_shot_trade_closes_owned_clients_when_state_store_init_fails(
    monkeypatch,
) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )

    class TrackingQuoteClient:
        def __init__(self):
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    class TrackingTradeClient:
        def __init__(self):
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    quote_client = TrackingQuoteClient()
    trade_client = TrackingTradeClient()

    monkeypatch.setattr(
        orchestrator,
        "MoomooOpenDClient",
        lambda host, port: quote_client,
    )
    monkeypatch.setattr(
        orchestrator,
        "MoomooPaperTradeClient",
        lambda host, port, trd_env: trade_client,
    )

    class FailingStateStore:
        def __init__(self, db_path=None, execution_mode=None):
            raise RuntimeError("state init failed")

    monkeypatch.setattr(orchestrator, "StateStore", FailingStateStore)

    with pytest.raises(RuntimeError, match="state init failed"):
        orchestrator.run_one_shot_trade(
            settings=settings,
            trade_env=TrdEnv.SIMULATE,
            symbols=["US.AAPL"],
            benchmark_symbol="US.VT",
            history_days=2200,
            capital=None,
            fx_jpy_per_usd=None,
            minimum_order_value=5.0,
        )

    assert quote_client.close_calls == 1
    assert trade_client.close_calls == 1


def test_reconcile_pending_orders_logs_type_error_and_returns_zero(
    monkeypatch, caplog
) -> None:
    state_store = FakeStateStore(
        pending_orders=[
            OrderRecord(
                order_id="pending-1",
                symbol="US.MSFT",
                side="BUY",
                quantity=2.0,
                price=250.0,
                status="submitted",
                reason="pending-test",
            )
        ]
    )
    trade_client = FakeTradeClient(
        order_frame=pd.DataFrame(
            {
                "order_id": ["pending-1"],
                "order_status": ["FILLED_ALL"],
                "code": ["US.MSFT"],
            }
        )
    )

    monkeypatch.setattr(
        orchestrator,
        "_broker_row_matches_order",
        lambda order_row, pending_order: (_ for _ in ()).throw(TypeError("boom")),
    )

    with caplog.at_level("WARNING"):
        reconciled = orchestrator._reconcile_pending_orders(state_store, trade_client)

    assert reconciled == 0
    assert "Skipping pending order reconcile due to unexpected row type" in caplog.text


def test_market_date_for_frame_raises_on_empty_frame() -> None:
    with pytest.raises(ValueError, match="Price frame is empty"):
        orchestrator._market_date_for_frame(pd.DataFrame(columns=["US.AAPL"]))


def test_market_date_for_frame_raises_on_nat_index() -> None:
    frame = pd.DataFrame(
        {"US.AAPL": [100.0]},
        index=pd.DatetimeIndex([pd.NaT]),
    )

    with pytest.raises(ValueError, match="cannot determine market date"):
        orchestrator._market_date_for_frame(frame)


def test_run_one_shot_trade_local_sim_uses_equity_for_account_value(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
        daily_loss_limit_pct=0.05,
    )
    quote_client = FakeQuoteClient()
    strategy = FakeStrategy()
    state_store = FakeStateStore(
        previous_equity=EquitySnapshot(
            timestamp="2026-04-23T00:00:00+00:00",
            account_value=100_000.0,
            cash=100_000.0,
            positions_json="{}",
            market_date="2026-04-23",
        )
    )

    sim_path = tmp_path / "paper-sim-state.json"
    monkeypatch.setattr(orchestrator_cycle, "_LOCAL_SIM_PATH", sim_path)

    from moomoo_bot.paper_simulator import PaperSimulator

    sim = PaperSimulator.load(state_path=sim_path, initial_cash=1_000.0)
    sim.place_market_order(symbol="US.AAPL", side="BUY", quantity=5.0, price=100.0)
    sim.save()

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        strategy=strategy,
        state_store=state_store,
        use_local_sim=True,
    )

    assert state_store.recorded_equity
    assert state_store.recorded_equity[-1]["account_value"] == pytest.approx(1027.8)
    assert not state_store.saved_states or not state_store.saved_states[-1].halted


def test_run_one_shot_trade_local_sim_persists_orders(monkeypatch, tmp_path) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    quote_client = FakeQuoteClient()
    strategy = FakeStrategy()
    state_store = FakeStateStore()

    sim_path = tmp_path / "paper-sim-state.json"
    monkeypatch.setattr(orchestrator_cycle, "_LOCAL_SIM_PATH", sim_path)

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        strategy=strategy,
        state_store=state_store,
        use_local_sim=True,
    )

    payload = json.loads(sim_path.read_text(encoding="utf-8"))
    assert payload["positions"]["US.AAPL"]["quantity"] == pytest.approx(947.0)
    assert len(payload["trades"]) == 1


def test_run_one_shot_trade_local_sim_bootstraps_empty_sim_from_jpy_capital(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="JPY",
        initial_capital=100_000.0,
        fx_jpy_per_usd=150.0,
    )
    quote_client = FakeQuoteClient()
    strategy = FakeStrategy()
    state_store = FakeStateStore()

    sim_path = tmp_path / "paper-sim-state.json"
    monkeypatch.setattr(orchestrator_cycle, "_LOCAL_SIM_PATH", sim_path)

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=100_000.0,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        strategy=strategy,
        state_store=state_store,
        use_local_sim=True,
    )

    payload = json.loads(sim_path.read_text(encoding="utf-8"))
    assert payload["cash"] == pytest.approx(33.31, abs=0.01)
    assert payload["positions"]["US.AAPL"]["quantity"] == pytest.approx(6.0)
    assert state_store.recorded_equity[-1]["account_value"] == pytest.approx(666.67, abs=0.01)


def test_run_one_shot_trade_local_sim_bootstrap_clears_stale_state_db(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="JPY",
        initial_capital=100_000.0,
        fx_jpy_per_usd=150.0,
    )
    quote_client = FakeQuoteClient()
    strategy = FakeStrategy()
    sim_path = tmp_path / "paper-sim-state.json"
    db_path = tmp_path / "paper-sim-state.db"

    monkeypatch.setattr(orchestrator_cycle, "_LOCAL_SIM_PATH", sim_path)
    monkeypatch.setattr(orchestrator_cycle, "_LOCAL_SIM_STATE_DB_PATH", db_path)

    seeded_store = StateStore(db_path=db_path, execution_mode="paper")
    seeded_store.save_risk_state(
        PersistentRiskState(
            peak_account_value=100_000.0,
            halted=True,
            halted_reason="max_drawdown:99.33% from 100000",
            drawdown_tier=2,
            daily_order_date="2026-04-24",
            last_equity_value=100_000.0,
        )
    )
    seeded_store.record_equity(
        account_value=100_000.0,
        cash=100_000.0,
        positions={},
        market_date="2026-04-24",
    )
    seeded_store.close()

    orchestrator.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=["US.AAPL"],
        benchmark_symbol="US.VT",
        history_days=2200,
        capital=100_000.0,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        quote_client=quote_client,
        strategy=strategy,
        use_local_sim=True,
    )

    verified_store = StateStore(db_path=db_path, execution_mode="paper")
    persisted = verified_store.load_risk_state()
    verified_store.close()
    assert persisted.halted is False
    assert persisted.halted_reason in (None, "")


@dataclass
class FakeQuoteClient:
    fetch_price_panel_calls: int = 0
    fetch_market_snapshot_calls: int = 0
    last_include_benchmark_in_prices: bool | None = None

    def fetch_price_panel(
        self,
        selected_symbols,
        benchmark_label,
        history_days=2200,
        include_benchmark_in_prices: bool = False,
    ):
        self.fetch_price_panel_calls += 1
        self.last_include_benchmark_in_prices = include_benchmark_in_prices
        index = pd.date_range("2025-01-01", periods=3, freq="B")
        price_frame = pd.DataFrame({"US.AAPL": [100.0, 101.0, 102.0]}, index=index)
        if include_benchmark_in_prices:
            price_frame["US.VT"] = [50.0, 51.0, 52.0]
        benchmark = pd.Series([50.0, 51.0, 52.0], index=index, name="benchmark")
        return price_frame, benchmark

    def fetch_market_state(self, code_list):
        return pd.DataFrame({"code": list(code_list), "market_state": ["MORNING"] * len(code_list)})

    def fetch_market_snapshot(self, code_list):
        self.fetch_market_snapshot_calls += 1
        return pd.DataFrame({"code": list(code_list), "last_price": [105.555] * len(code_list)})

    def close(self) -> None:
        return None


@dataclass
class FakeTradeClient:
    submit_order_calls: int = 0
    get_order_frame_calls: int = 0
    last_instruction_price: float | None = None
    account_value: float = 100_000.0
    buying_power: float | None = None
    submitted_instructions: list[object] = field(default_factory=list)
    order_frame: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(
            {"order_id": [], "order_status": [], "code": [], "trd_side": [], "qty": [], "price": [], "filled_quantity": [], "remark": []}
        )
    )
    position_frame: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame({"code": [], "qty": []})
    )
    submitted_reasons: list[str] = field(default_factory=list)

    def get_position_frame(self):
        return self.position_frame.copy()

    def get_order_frame(self, refresh_cache: bool = True):
        self.get_order_frame_calls += 1
        return self.order_frame.copy()

    def get_account_value(self):
        return self.account_value
    def get_buying_power(self):
        return self.buying_power if self.buying_power is not None else self.account_value

    def get_matching_active_order(self, instruction, refresh_cache=True):
        return None

    def submit_order(self, instruction):
        self.submit_order_calls += 1
        self.last_instruction_price = instruction.price
        self.submitted_reasons.append(instruction.reason)
        self.submitted_instructions.append(instruction)
        return pd.DataFrame({"order_id": [self.submit_order_calls], "order_status": ["FILLED"]})

    def close(self) -> None:
        return None


@dataclass
class FakeStateStore:
    risk_state: PersistentRiskState = field(default_factory=PersistentRiskState)
    previous_equity: EquitySnapshot | None = None
    saved_states: list[PersistentRiskState] = field(default_factory=list)
    recorded_equity: list[dict[str, object]] = field(default_factory=list)
    recorded_positions: list[dict[str, object]] = field(default_factory=list)
    recorded_orders: list[OrderRecord] = field(default_factory=list)
    pending_orders: list[OrderRecord] = field(default_factory=list)
    order_status_updates: list[tuple[str, str, float]] = field(default_factory=list)
    order_status_update_details: list[dict[str, object]] = field(default_factory=list)
    cleanup_calls: list[int] = field(default_factory=list)

    def load_risk_state(self) -> PersistentRiskState:
        return PersistentRiskState(**self.risk_state.__dict__)

    def save_risk_state(self, state: PersistentRiskState) -> None:
        snapshot = PersistentRiskState(**state.__dict__)
        self.risk_state = snapshot
        self.saved_states.append(snapshot)

    def get_latest_equity_before_market_date(
        self, market_date: str
    ) -> EquitySnapshot | None:
        if self.previous_equity is None:
            return None
        if (
            self.previous_equity.market_date is not None
            and self.previous_equity.market_date < market_date
        ):
            return self.previous_equity
        return None

    def record_equity(
        self,
        account_value: float,
        cash: float,
        positions: dict[str, float],
        market_date: str | None = None,
    ) -> None:
        self.recorded_equity.append(
            {
                "account_value": account_value,
                "cash": cash,
                "positions": dict(positions),
                "market_date": market_date,
            }
        )

    def record_positions(
        self, positions: dict[str, float], prices: dict[str, float]
    ) -> None:
        self.recorded_positions.append(
            {"positions": dict(positions), "prices": dict(prices)}
        )

    def record_order(self, record: OrderRecord) -> int:
        snapshot = OrderRecord(**record.__dict__)
        self.recorded_orders.append(snapshot)
        return len(self.recorded_orders)

    def get_pending_orders(self) -> list[OrderRecord]:
        return [OrderRecord(**order.__dict__) for order in self.pending_orders]

    def update_order_status(
        self,
        order_id: str,
        status: str,
        filled_quantity: float,
        fill_price: float | None = None,
        broker_accepted_price: float | None = None,
        fee_amount: float | None = None,
        filled_at: str | None = None,
    ) -> None:
        self.order_status_updates.append((str(order_id), status, filled_quantity))
        self.order_status_update_details.append(
            {
                "fill_price": fill_price,
                "broker_accepted_price": broker_accepted_price,
                "fee_amount": fee_amount,
                "filled_at": filled_at,
            }
        )
        for index, order in enumerate(self.pending_orders):
            if str(order.order_id) != str(order_id):
                continue
            self.pending_orders[index] = OrderRecord(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=order.price,
                status=status,
                reason=order.reason,
                filled_quantity=filled_quantity,
                submitted_at=order.submitted_at,
                filled_at=order.filled_at,
                broker_accepted_price=broker_accepted_price,
                avg_fill_price=fill_price,
                cumulative_fee_amount=fee_amount or 0.0,
            )
            break

    def cleanup_old_equity(self, keep_days: int = 365) -> int:
        self.cleanup_calls.append(keep_days)
        return 0

    def get_equity_at_month_start(self, market_date: str):
        return None

    def get_recent_realizations(self, n: int):
        return []

    def close(self) -> None:
        return None


@dataclass
class FakeStrategy:
    decide_calls: int = 0

    def decide(self, prices, as_of):
        self.decide_calls += 1
        return TradeDecision(as_of=as_of, target_weights={"US.AAPL": 1.0}, reason="injected-test")
