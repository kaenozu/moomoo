from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import moomoo_bot.ui.paper_studio as paper_studio
from moomoo_bot.config import Settings
from moomoo_bot.paper_simulator import PaperSimulator
from moomoo_bot.ui.paper_studio import (
    _build_execution_report,
    _format_trade_summary_line,
)


def test_format_trade_summary_line_uses_japanese_action_words() -> None:
    buy_trade = SimpleNamespace(symbol="US.VT", side="BUY", quantity=1.0)
    sell_trade = SimpleNamespace(symbol="US.AMD", side="SELL", quantity=3.0)

    assert _format_trade_summary_line(buy_trade) == "US.VT を 1株購入"
    assert _format_trade_summary_line(sell_trade) == "US.AMD を 3株売却"


def test_build_execution_report_formats_new_trades(tmp_path) -> None:
    sim_path = tmp_path / "paper-sim-state.json"
    simulator = PaperSimulator.load(state_path=sim_path, initial_cash=100_000.0)
    simulator.place_market_order(
        symbol="US.VT",
        side="BUY",
        quantity=1.0,
        price=150.34,
    )
    simulator.mark_to_market({"US.VT": 150.34})

    report = _build_execution_report(0, simulator)

    assert report["trade_count"] == 1
    assert report["summary_lines"] == ["US.VT を 1株購入"]
    assert report["rows"][0]["銘柄"] == "US.VT"
    assert report["rows"][0]["売買"] == "購入"


def test_run_strategy_from_ui_uses_run_paper_trade_defaults(monkeypatch, tmp_path) -> None:
    settings = Settings(
        symbols="US.AAPL,US.MSFT",
        benchmark_symbol="US.VT",
        execution_mode="paper",
        capital_currency="USD",
        initial_capital=100_000.0,
    )
    state_path = tmp_path / "paper-sim-state.json"
    expected_state_path = state_path
    fake_trade = SimpleNamespace(
        symbol="US.AAPL",
        side="BUY",
        quantity=1.0,
        price=100.0,
        notional=100.0,
        realized_pnl=0.0,
    )
    fake_simulator = SimpleNamespace(
        trades=[],
        cash=100_000.0,
        positions={},
        equity_curve=[],
    )
    fake_cycle = SimpleNamespace(__name__="moomoo_bot.orchestrator.cycle")

    def fake_load(*, state_path, initial_cash):
        assert state_path == expected_state_path
        assert initial_cash == 100_000.0
        return fake_simulator

    class FakeOrchestratorModule:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def run_one_shot_trade(self, **kwargs) -> None:
            self.calls.append(kwargs)
            fake_simulator.trades.append(fake_trade)

    fake_orchestrator = FakeOrchestratorModule()
    reload_calls: list[str] = []

    def fake_reload(module):
        reload_calls.append(module.__name__)
        if module.__name__ == "moomoo_bot.orchestrator.cycle":
            return fake_cycle
        return fake_orchestrator

    monkeypatch.setattr(paper_studio.importlib, "reload", fake_reload)
    monkeypatch.setattr(paper_studio.PaperSimulator, "load", fake_load)

    report = paper_studio._run_strategy_from_ui(
        settings=settings,
        state_path_text=str(state_path),
        initial_cash=100_000.0,
    )

    assert reload_calls == ["moomoo_bot.orchestrator.cycle", "moomoo_bot.orchestrator"]
    assert len(fake_orchestrator.calls) == 1
    call = fake_orchestrator.calls[0]
    assert call["symbols"] == settings.symbol_list
    assert call["benchmark_symbol"] == settings.benchmark_symbol
    assert call["history_days"] == 2200
    assert call["capital"] == 100_000.0
    assert call["fx_jpy_per_usd"] is None
    assert call["minimum_order_value"] == 5.0
    assert call["use_local_sim"] is True
    assert call["local_sim_path"] == state_path
    assert call["local_sim_state_db_path"] == state_path.with_suffix(".db")
    assert report["trade_count"] == 1
    assert report["summary_lines"] == ["US.AAPL を 1株購入"]


def test_load_simulator_reloads_when_state_file_changes(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "paper-sim-state.json"
    session_state: dict[str, object] = {}
    load_calls: list[str] = []
    mtime_ns = {"value": 1}

    class FakeSimulator:
        def __init__(self, marker: int) -> None:
            self.marker = marker

    def fake_load(*, state_path, initial_cash):
        load_calls.append(str(state_path))
        return FakeSimulator(len(load_calls))

    def fake_mtime(path):
        return mtime_ns["value"]

    monkeypatch.setattr(paper_studio.st, "session_state", session_state, raising=False)
    monkeypatch.setattr(paper_studio.PaperSimulator, "load", fake_load)
    monkeypatch.setattr(paper_studio, "_simulator_state_mtime_ns", fake_mtime)

    first = paper_studio._load_simulator(state_path, 100_000.0)
    second = paper_studio._load_simulator(state_path, 100_000.0)

    mtime_ns["value"] = 2
    third = paper_studio._load_simulator(state_path, 100_000.0)

    assert first is second
    assert second is not third
    assert [sim.marker for sim in (first, second, third)] == [1, 1, 2]
    assert load_calls == [str(state_path), str(state_path)]


def test_apply_styles_uses_dark_theme_tokens(monkeypatch) -> None:
    markdown_calls: list[str] = []

    def fake_markdown(body, *args, **kwargs) -> None:
        markdown_calls.append(str(body))

    monkeypatch.setattr(paper_studio.st, "markdown", fake_markdown)

    paper_studio._apply_styles()

    rendered_css = "\n".join(markdown_calls)
    assert "color-scheme: dark" in rendered_css
    assert "--bg: #0b1220" in rendered_css
    assert "linear-gradient(180deg, #020617 0%, var(--bg) 100%)" in rendered_css


def test_equity_history_preserves_unrealized_pnl(monkeypatch, tmp_path) -> None:
    session_state: dict[str, object] = {}
    monkeypatch.setattr(paper_studio.st, "session_state", session_state, raising=False)

    paper_studio._append_live_equity("2026-04-27T00:00:00+00:00", 10125.0, 125.0)
    live_frame = paper_studio._live_equity_frame()

    simulator = PaperSimulator.load(state_path=tmp_path / "paper-sim-state.json", initial_cash=10_000.0)
    simulator.place_market_order(symbol="US.AAPL", side="BUY", quantity=10.0, price=100.0)
    simulator.mark_to_market({"US.AAPL": 112.5})

    stored_frame = paper_studio._equity_df(simulator)

    assert list(live_frame.columns) == ["timestamp", "equity", "unrealized_pnl"]
    assert live_frame["unrealized_pnl"].iloc[0] == pytest.approx(125.0)
    assert "unrealized_pnl" in stored_frame.columns
    assert stored_frame["unrealized_pnl"].iloc[0] == pytest.approx(125.0)


def test_equity_chart_frames_split_scale_sensitive_series() -> None:
    live_equity_df = pd.DataFrame(
        [
            {
                "timestamp": "2026-04-27T00:00:00+00:00",
                "equity": 10125.0,
                "unrealized_pnl": 125.0,
            }
        ]
    )
    stored_equity_df = pd.DataFrame(
        [
            {
                "timestamp": "2026-04-27T00:01:00+00:00",
                "equity": 10150.0,
                "unrealized_pnl": 150.0,
            }
        ]
    )

    equity_frame, pnl_frame = paper_studio._equity_chart_frames(live_equity_df, stored_equity_df)

    assert list(equity_frame.columns) == ["equity"]
    assert list(pnl_frame.columns) == ["unrealized_pnl"]
    assert equity_frame.iloc[0, 0] == pytest.approx(10125.0)
    assert pnl_frame.iloc[0, 0] == pytest.approx(125.0)


def test_seed_price_history_if_needed_loads_open_d_history(monkeypatch) -> None:
    session_state: dict[str, object] = {}
    fetched_calls: list[tuple[str, str, str]] = []

    class FakeQuoteClient:
        def __init__(self, host, port) -> None:
            self.host = host
            self.port = port

        def fetch_history(self, symbol, start=None, end=None, max_count=1000):
            fetched_calls.append((symbol, start, end))
            return pd.DataFrame(
                {"close": [100.0, 110.0, 105.0]},
                index=pd.to_datetime(
                    ["2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00", "2026-01-03T00:00:00+00:00"]
                ),
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(paper_studio.st, "session_state", session_state, raising=False)
    monkeypatch.setattr(paper_studio, "MoomooOpenDClient", FakeQuoteClient)

    paper_studio._seed_price_history_if_needed("US.VT", "127.0.0.1", 11111)

    frame = paper_studio._price_history_frame("US.VT")

    assert fetched_calls and fetched_calls[0][0] == "US.VT"
    assert list(frame.columns) == ["timestamp", "price"]
    assert len(frame) == 3
    assert frame["price"].tolist() == [100.0, 110.0, 105.0]
    assert session_state[paper_studio._price_history_seed_key("US.VT")] is True


def test_price_history_frame_handles_mixed_timestamp_formats(monkeypatch) -> None:
    session_state: dict[str, object] = {
        paper_studio._price_history_key("US.VT"): [
            {"timestamp": "2026-04-27T00:00:00+00:00", "price": 150.10},
            {"timestamp": "2026-04-27T00:00:00.548124+00:00", "price": 150.34},
            {"timestamp": "2026-04-27T00:00:01", "price": 150.50},
        ]
    }
    monkeypatch.setattr(paper_studio.st, "session_state", session_state, raising=False)

    frame = paper_studio._price_history_frame("US.VT")

    assert len(frame) == 3
    assert frame["price"].tolist() == [150.10, 150.34, 150.50]
    assert frame["timestamp"].is_monotonic_increasing
