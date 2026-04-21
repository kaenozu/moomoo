from __future__ import annotations

import pandas as pd

from moomoo import Session, TrdSide

from moomoo_bot.paper import build_paper_plan, build_paper_rebalance_orders
from moomoo_bot.strategy.base import TradeDecision


def test_build_paper_plan_sizes_fractional_orders_and_skips_small_allocations() -> None:
    index = pd.date_range("2025-01-01", periods=3, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [100.0, 101.0, 100.0],
            "US.MSFT": [200.0, 201.0, 200.0],
            "US.NVDA": [50.0, 50.5, 50.0],
        },
        index=index,
    )
    decision = TradeDecision(
        as_of=index[-1],
        target_weights={"US.AAPL": 0.6, "US.MSFT": 0.399, "US.NVDA": 0.001},
        reason="monthly_top_momentum:US.AAPL,US.MSFT",
    )

    plan = build_paper_plan(prices, decision, capital=1000.0, minimum_order_value=5.0)

    assert [allocation.symbol for allocation in plan.allocations] == ["US.AAPL", "US.MSFT"]
    assert plan.allocations[0].target_quantity == 6.0
    assert plan.allocations[0].target_cost == 600.0
    assert plan.allocations[1].target_quantity == 1.995
    assert plan.allocations[1].target_cost == 399.0
    assert plan.cash_remaining == 1.0


def test_build_paper_plan_rejects_non_positive_capital() -> None:
    index = pd.date_range("2025-01-01", periods=2, freq="B")
    prices = pd.DataFrame({"US.AAPL": [100.0, 101.0]}, index=index)
    decision = TradeDecision(as_of=index[-1], target_weights={}, reason="cash")

    try:
        build_paper_plan(prices, decision, capital=0.0)
    except ValueError as exc:
        assert str(exc) == "capital must be positive"
    else:
        raise AssertionError("expected ValueError")


def test_build_paper_plan_caps_single_position_when_requested() -> None:
    index = pd.date_range("2025-01-01", periods=3, freq="B")
    prices = pd.DataFrame({"US.AAPL": [100.0, 101.0, 100.0]}, index=index)
    decision = TradeDecision(as_of=index[-1], target_weights={"US.AAPL": 1.0}, reason="monthly_top_momentum:US.AAPL")

    plan = build_paper_plan(prices, decision, capital=1000.0, max_position_weight=0.35)

    assert plan.allocations[0].target_cost == 350.0
    assert plan.cash_remaining == 650.0


def test_build_paper_rebalance_orders_sells_excess_and_buys_missing_positions() -> None:
    index = pd.date_range("2025-01-01", periods=3, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [100.0, 101.0, 100.0],
            "US.MSFT": [200.0, 201.0, 200.0],
        },
        index=index,
    )
    decision = TradeDecision(
        as_of=index[-1],
        target_weights={"US.AAPL": 0.5, "US.MSFT": 0.5},
        reason="monthly_top_momentum:US.AAPL,US.MSFT",
    )

    plan = build_paper_plan(prices, decision, capital=1000.0)
    instructions = build_paper_rebalance_orders(
        plan,
        current_positions={"US.AAPL": 7.0, "US.MSFT": 0.0, "US.TSLA": 2.0},
        latest_prices={"US.AAPL": 100.0, "US.MSFT": 200.0, "US.TSLA": 150.0},
    )

    assert [instruction.side for instruction in instructions] == [TrdSide.SELL, TrdSide.SELL, TrdSide.BUY]
    assert [instruction.symbol for instruction in instructions] == ["US.TSLA", "US.AAPL", "US.MSFT"]
    assert instructions[0].quantity == 2.0
    assert instructions[1].quantity == 2.0
    assert instructions[2].quantity == 2.5


def test_build_paper_rebalance_orders_uses_eth_session_when_market_closed() -> None:
    index = pd.date_range("2025-01-01", periods=3, freq="B")
    prices = pd.DataFrame(
        {
            "US.AAPL": [100.0, 101.0, 100.0],
            "US.MSFT": [200.0, 201.0, 200.0],
        },
        index=index,
    )
    decision = TradeDecision(
        as_of=index[-1],
        target_weights={"US.AAPL": 0.5, "US.MSFT": 0.5},
        reason="monthly_top_momentum:US.AAPL,US.MSFT",
    )

    plan = build_paper_plan(prices, decision, capital=1000.0)
    instructions = build_paper_rebalance_orders(plan, market_open=False)

    assert all(instruction.session == Session.ETH for instruction in instructions)
    assert all(instruction.fill_outside_rth is True for instruction in instructions)