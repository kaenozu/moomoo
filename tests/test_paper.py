from __future__ import annotations

import pandas as pd

from moomoo import Session, TrdSide

from moomoo_bot.broker.paper import MoomooPaperTradeClient
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
    assert instructions[2].quantity == 2.0


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


def test_get_matching_active_order_returns_only_active_orders() -> None:
    instruction = build_paper_rebalance_orders(
        build_paper_plan(
            pd.DataFrame({"US.AAPL": [100.0, 101.0, 102.0]}, index=pd.date_range("2025-01-01", periods=3, freq="B")),
            TradeDecision(as_of=pd.Timestamp("2025-01-03"), target_weights={"US.AAPL": 1.0}, reason="monthly_top_momentum:US.AAPL"),
            capital=102_000.0,
        )
    )[0]

    active_frame = pd.DataFrame(
        {
            "order_id": ["ACTIVE-1", "DONE-1"],
            "order_status": ["SUBMITTED", "FILLED_ALL"],
            "code": ["US.AAPL", "US.AAPL"],
            "trd_side": ["BUY", "BUY"],
            "qty": [1000.0, 1000.0],
            "price": [102.0, 102.0],
            "session": ["N/A", "N/A"],
            "fill_outside_rth": [False, False],
            "remark": ["monthly_top_momentum:US.AAPL", "monthly_top_momentum:US.AAPL"],
        }
    )

    client = MoomooPaperTradeClient(trade_context=FakeTradeContext(active_frame))

    matching_order = client.get_matching_active_order(instruction)

    assert matching_order is not None
    assert matching_order["order_id"] == "ACTIVE-1"


class FakeTradeContext:
    def __init__(self, order_frame: pd.DataFrame) -> None:
        self.order_frame = order_frame

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None

    def order_list_query(self, trd_env, refresh_cache=True):
        return 0, self.order_frame