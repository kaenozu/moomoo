from __future__ import annotations

from pathlib import Path

import pytest

from moomoo_bot.paper_simulator import PaperSimulator


def test_buy_sell_and_pnl_flow(tmp_path: Path) -> None:
    simulator = PaperSimulator(state_path=tmp_path / "paper-sim-state.json", initial_cash=10_000.0)

    buy = simulator.place_market_order(symbol="US.AAPL", side="BUY", quantity=10.0, price=100.0)
    assert buy.notional == 1_000.0
    assert simulator.cash == 9_000.0

    snapshot = simulator.mark_to_market({"US.AAPL": 105.0})
    assert snapshot.equity == pytest.approx(10_050.0)
    assert snapshot.unrealized_pnl == pytest.approx(50.0)

    sell = simulator.place_market_order(symbol="US.AAPL", side="SELL", quantity=4.0, price=110.0)
    assert sell.realized_pnl == pytest.approx(40.0)
    assert simulator.realized_pnl == pytest.approx(40.0)
    assert simulator.positions["US.AAPL"].quantity == pytest.approx(6.0)


def test_rejects_insufficient_cash_and_position(tmp_path: Path) -> None:
    simulator = PaperSimulator(state_path=tmp_path / "paper-sim-state.json", initial_cash=100.0)

    with pytest.raises(ValueError, match="insufficient cash"):
        simulator.place_market_order(symbol="US.AAPL", side="BUY", quantity=2.0, price=60.0)

    simulator.place_market_order(symbol="US.AAPL", side="BUY", quantity=1.0, price=50.0)
    with pytest.raises(ValueError, match="insufficient position"):
        simulator.place_market_order(symbol="US.AAPL", side="SELL", quantity=2.0, price=55.0)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "paper-sim-state.json"
    simulator = PaperSimulator(state_path=path, initial_cash=5_000.0)
    simulator.place_market_order(symbol="US.MSFT", side="BUY", quantity=5.0, price=200.0)
    simulator.mark_to_market({"US.MSFT": 210.0})
    simulator.save()

    restored = PaperSimulator.load(state_path=path, initial_cash=1.0)
    assert restored.cash == pytest.approx(simulator.cash)
    assert restored.realized_pnl == pytest.approx(simulator.realized_pnl)
    assert restored.positions["US.MSFT"].quantity == pytest.approx(5.0)
    assert len(restored.trades) == 1
    assert len(restored.equity_curve) == 1
