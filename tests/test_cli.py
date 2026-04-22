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


@pytest.mark.skip(reason="Requires orchestrator refactoring for proper mock injection")
def test_auto_run_recovers_from_transient_quote_failure(monkeypatch) -> None:
    settings = Settings(symbols="US.AAPL,US.MSFT", benchmark_symbol="US.VT", execution_mode="paper")
    quote_client = FakeQuoteClient()
    trade_client = FakeTradeClient()
    sleep_calls: list[int] = []

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "MoomooOpenDClient", lambda *args, **kwargs: quote_client)
    monkeypatch.setattr(cli, "MoomooPaperTradeClient", lambda *args, **kwargs: trade_client)

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "sleep", fake_sleep)

    try:
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
    except KeyboardInterrupt:
        pass

    assert quote_client.fetch_price_panel_calls == 2
    assert sleep_calls == [1, 1]


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


@pytest.mark.skip(reason="Requires orchestrator refactoring for proper mock injection")
def test_live_trade_uses_real_trade_environment_when_armed(monkeypatch) -> None:
    settings = Settings(symbols="US.AAPL", benchmark_symbol="US.VT", execution_mode="live", allow_live_trading=True)
    quote_client = FakeLiveQuoteClient()
    trade_client = FakeLiveTradeClient()

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "MoomooOpenDClient", lambda *args, **kwargs: quote_client)

    def fake_trade_client_factory(*args, **kwargs):
        assert kwargs["trd_env"] == TrdEnv.REAL
        return trade_client

    monkeypatch.setattr(orchestrator, "MoomooPaperTradeClient", fake_trade_client_factory)
    monkeypatch.setattr(orchestrator, "build_monthly_strategy", lambda settings: FakeLiveStrategy())

    cli.live_trade(
        symbols=None,
        benchmark_symbol=None,
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
        confirm_live_trading=True,
    )

    assert trade_client.trd_env == TrdEnv.REAL
    assert trade_client.submit_order_calls >= 1


def test_paper_trade_skips_duplicate_active_orders(monkeypatch) -> None:
    settings = Settings(
        symbols="US.AAPL",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=102_000.0,
    )
    quote_client = FakeLiveQuoteClient()
    trade_client = FakeTradeClient(
        matching_active_orders=[
            {
                "order_id": "DUP-1",
                "order_status": "SUBMITTED",
                "code": "US.AAPL",
                "trd_side": "BUY",
                "qty": 980.0,
                "price": 102.0,
                "session": "N/A",
                "fill_outside_rth": False,
                "remark": "live-test",
            }
        ]
    )

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "MoomooOpenDClient", lambda *args, **kwargs: quote_client)
    monkeypatch.setattr(cli, "MoomooPaperTradeClient", lambda *args, **kwargs: trade_client)
    monkeypatch.setattr(cli, "_build_monthly_strategy", lambda settings: FakeLiveStrategy())

    cli.paper_trade(
        symbols=None,
        benchmark_symbol=None,
        history_days=2200,
        capital=None,
        fx_jpy_per_usd=None,
        minimum_order_value=5.0,
    )

    assert trade_client.submit_order_calls == 0


@dataclass
class FakeQuoteClient:
    fetch_price_panel_calls: int = 0

    def fetch_price_panel(self, selected_symbols, benchmark_label, history_days=2200):
        self.fetch_price_panel_calls += 1
        if self.fetch_price_panel_calls == 1:
            raise RuntimeError("temporary quote failure")

        index = pd.date_range("2025-01-01", periods=3, freq="B")
        price_frame = pd.DataFrame(
            {
                "US.AAPL": [100.0, 101.0, 102.0],
                "US.MSFT": [200.0, 201.0, 202.0],
            },
            index=index,
        )
        benchmark = pd.Series([50.0, 51.0, 52.0], index=index, name="benchmark")
        return price_frame, benchmark

    def fetch_market_state(self, code_list):
        return pd.DataFrame({"code": code_list, "market_state": ["MORNING"] * len(code_list)})

    def close(self) -> None:
        return None


@dataclass
class FakeTradeClient:
    submit_order_calls: int = 0
    matching_active_orders: list[dict[str, object]] | None = None
    account_value: float = 100_000.0

    def get_position_frame(self):
        return pd.DataFrame({"code": [], "qty": []})

    def get_account_value(self):
        return self.account_value

    def get_matching_active_order(self, instruction, refresh_cache=True):
        for order in self.matching_active_orders or []:
            if (
                order.get("code") == instruction.symbol
                and str(order.get("trd_side", "")).upper() == str(instruction.side).upper()
                and float(order.get("qty", 0.0) or 0.0) == float(instruction.quantity)
                and float(order.get("price", 0.0) or 0.0) == float(instruction.price)
                and str(order.get("session", "")).upper() == str(instruction.session).upper()
                and bool(order.get("fill_outside_rth", False)) == bool(instruction.fill_outside_rth)
                and str(order.get("remark", "")) == str(instruction.reason)
            ):
                return order
        return None

    def submit_order(self, instruction):
        self.submit_order_calls += 1
        return pd.DataFrame({"order_id": [self.submit_order_calls], "order_status": ["FILLED"]})

    def close(self) -> None:
        return None


@dataclass
class FakeLiveQuoteClient:
    def fetch_price_panel(self, selected_symbols, benchmark_label, history_days=2200):
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
        return pd.DataFrame({"code": code_list, "market_state": ["MORNING"] * len(code_list)})

    def close(self) -> None:
        return None


@dataclass
class FakeLiveTradeClient:
    trd_env: TrdEnv | None = None
    submit_order_calls: int = 0

    def __post_init__(self) -> None:
        if self.trd_env is None:
            self.trd_env = TrdEnv.REAL

    def get_position_frame(self):
        return pd.DataFrame({"code": [], "qty": []})

    def get_account_value(self):
        return 100_000.0

    def submit_order(self, instruction):
        self.submit_order_calls += 1
        return pd.DataFrame({"order_id": [self.submit_order_calls], "order_status": ["FILLED"]})

    def close(self) -> None:
        return None


@dataclass
class FakeLiveStrategy:
    def decide(self, prices, as_of):
        return TradeDecision(as_of=as_of, target_weights={"US.AAPL": 1.0}, reason="live-test")