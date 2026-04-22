from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from moomoo import TrdEnv

from moomoo_bot import orchestrator
from moomoo_bot.config import Settings
from moomoo_bot.paper import PaperPlan
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
    )

    assert strategy.decide_calls == 1
    assert quote_client.fetch_price_panel_calls == 1
    assert trade_client.submit_order_calls == 1


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
    )

    assert quote_client.fetch_market_snapshot_calls == 1
    assert trade_client.last_instruction_price == 105.56


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

    def fake_build_paper_plan(prices, decision, capital, minimum_order_value=5.0, max_position_weight=1.0):
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
        max_position_weight=0.35,
        quote_client=quote_client,
        trade_client=trade_client,
        strategy=strategy,
    )

    assert captured["max_position_weight"] == 0.35


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
        )
    except KeyboardInterrupt:
        pass

    assert strategy.decide_calls == 1
    assert quote_client.fetch_price_panel_calls == 1
    assert trade_client.submit_order_calls == 1
    assert sleep_calls == [1]


@dataclass
class FakeQuoteClient:
    fetch_price_panel_calls: int = 0
    fetch_market_snapshot_calls: int = 0

    def fetch_price_panel(self, selected_symbols, benchmark_label, history_days=2200):
        self.fetch_price_panel_calls += 1
        index = pd.date_range("2025-01-01", periods=3, freq="B")
        price_frame = pd.DataFrame(
            {
                "US.AAPL": [100.0, 101.0, 102.0],
                "US.VT": [50.0, 51.0, 52.0],
            },
            index=index,
        )
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
    last_instruction_price: float | None = None

    def get_position_frame(self):
        return pd.DataFrame({"code": [], "qty": []})

    def get_account_value(self):
        return 100_000.0

    def get_matching_active_order(self, instruction, refresh_cache=True):
        return None

    def submit_order(self, instruction):
        self.submit_order_calls += 1
        self.last_instruction_price = instruction.price
        return pd.DataFrame({"order_id": [self.submit_order_calls], "order_status": ["FILLED"]})

    def close(self) -> None:
        return None


@dataclass
class FakeStrategy:
    decide_calls: int = 0

    def decide(self, prices, as_of):
        self.decide_calls += 1
        return TradeDecision(as_of=as_of, target_weights={"US.AAPL": 1.0}, reason="injected-test")
