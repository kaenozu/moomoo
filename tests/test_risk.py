from __future__ import annotations

import pandas as pd
from moomoo import Session

from moomoo_bot.risk import (
    RiskState,
    build_liquidation_orders,
    build_stop_loss_take_profit_orders,
    detect_market_shock,
    update_drawdown_state,
)


def test_detect_market_shock_triggers_on_large_benchmark_drop() -> None:
    benchmark = pd.Series(
        [100.0, 94.0], index=pd.date_range("2025-01-01", periods=2, freq="B")
    )

    reason = detect_market_shock(benchmark, drop_pct=0.05)

    assert reason is not None
    assert reason.startswith("market_shock:")


def test_update_drawdown_state_tracks_peak_and_halts_on_threshold_breach() -> None:
    state = RiskState()

    assert update_drawdown_state(100_000.0, state, max_drawdown_pct=0.10) is None
    assert state.peak_account_value == 100_000.0
    assert update_drawdown_state(92_000.0, state, max_drawdown_pct=0.10) is None
    reason = update_drawdown_state(89_000.0, state, max_drawdown_pct=0.10)

    assert reason is not None
    assert state.halted is True
    assert state.halted_reason == reason


def test_build_stop_loss_take_profit_orders_exits_breached_positions() -> None:
    position_rows = pd.DataFrame(
        {
            "code": "US.AAPL US.MSFT".split(),
            "qty": [10.0, 5.0],
            "cost_price": [100.0, 100.0],
        }
    )
    latest_prices = {"US.AAPL": 89.0, "US.MSFT": 125.0}

    orders = build_stop_loss_take_profit_orders(
        position_rows, latest_prices, stop_loss_pct=0.10, take_profit_pct=0.20
    )

    assert [order.symbol for order in orders] == ["US.AAPL", "US.MSFT"]
    assert [order.quantity for order in orders] == [10.0, 5.0]
    assert [order.reason.split(":")[1] for order in orders] == [
        "stop_loss",
        "take_profit",
    ]


def test_build_liquidation_orders_sells_all_positions() -> None:
    orders = build_liquidation_orders(
        {"US.AAPL": 3.0, "US.MSFT": 1.5},
        {"US.AAPL": 90.0, "US.MSFT": 80.0},
        "risk:halt",
    )

    assert [order.symbol for order in orders] == ["US.AAPL", "US.MSFT"]
    assert [order.quantity for order in orders] == [3.0, 1.5]


def test_build_liquidation_orders_can_route_closed_market_sells_to_eth() -> None:
    orders = build_liquidation_orders(
        {"US.AAPL": 3.0},
        {"US.AAPL": 90.0},
        "risk:halt",
        session=Session.ETH,
        fill_outside_rth=True,
    )

    assert orders[0].session == Session.ETH
    assert orders[0].fill_outside_rth is True


def test_build_liquidation_orders_respect_fractional_precision() -> None:
    orders = build_liquidation_orders(
        {"US.AAPL": 0.36},
        {"US.AAPL": 90.0},
        "risk:halt",
        fractional_share_precision=10.0,
    )

    assert orders[0].quantity == 0.3


def test_build_stop_loss_take_profit_orders_can_route_closed_market_exits_to_eth() -> (
    None
):
    position_rows = pd.DataFrame(
        {
            "code": ["US.AAPL"],
            "qty": [10.0],
            "cost_price": [100.0],
        }
    )
    latest_prices = {"US.AAPL": 89.0}

    orders = build_stop_loss_take_profit_orders(
        position_rows,
        latest_prices,
        stop_loss_pct=0.10,
        take_profit_pct=0.20,
        session=Session.ETH,
        fill_outside_rth=True,
    )

    assert orders[0].session == Session.ETH
    assert orders[0].fill_outside_rth is True


def test_build_stop_loss_take_profit_orders_respect_fractional_precision() -> None:
    position_rows = pd.DataFrame(
        {
            "code": ["US.AAPL"],
            "qty": [0.36],
            "cost_price": [100.0],
        }
    )
    latest_prices = {"US.AAPL": 89.0}

    orders = build_stop_loss_take_profit_orders(
        position_rows,
        latest_prices,
        stop_loss_pct=0.10,
        take_profit_pct=0.20,
        fractional_share_precision=10.0,
    )

    assert orders[0].quantity == 0.3
