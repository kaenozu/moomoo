from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from moomoo import TrdSide

from moomoo_bot.cli_helpers import submit_orders_with_duplicate_guard
from moomoo_bot.exceptions import OrderRejectedError
from moomoo_bot.paper import PaperOrderInstruction
from moomoo_bot.state import OrderRecord, StateStore


class FakeTradeClient:
    def __init__(self, response: pd.DataFrame) -> None:
        self.response = response
        self.submit_calls = 0

    def get_matching_active_order(self, instruction, refresh_cache: bool = True):
        return None

    def submit_order(self, instruction):
        self.submit_calls += 1
        return self.response.copy()


class RejectedTradeClient:
    def __init__(self, message: str) -> None:
        self.message = message
        self.submit_calls = 0

    def get_matching_active_order(self, instruction, refresh_cache: bool = True):
        return None

    def submit_order(self, instruction):
        self.submit_calls += 1
        raise OrderRejectedError(self.message)


def test_submit_orders_records_immediate_fill_into_execution_ledger(tmp_path) -> None:
    state_store = StateStore(db_path=tmp_path / "state.db")
    trade_client = FakeTradeClient(
        pd.DataFrame(
            {
                "order_id": ["fill-1"],
                "order_status": ["FILLED_ALL"],
                "filled_quantity": [2.0],
                "avg_fill_price": [101.0],
                "price": [100.0],
                "commission": [0.4],
                "updated_time": ["2025-01-03T14:30:00+00:00"],
            }
        )
    )
    instruction = PaperOrderInstruction(
        symbol="US.AAPL",
        side=TrdSide.BUY,
        quantity=2.0,
        price=100.0,
        reason="rebalance",
    )

    try:
        submit_orders_with_duplicate_guard(
            trade_client,
            [instruction],
            "paper",
            lambda *_args, **_kwargs: None,
            state_store=state_store,
        )

        fills = state_store.get_execution_fills(order_id="fill-1")
        lots = state_store.get_open_tax_lots(symbol="US.AAPL")
        recent_order = state_store.load_recent_orders(limit=1)[0]

        assert trade_client.submit_calls == 1
        assert recent_order.status == "filled_all"
        assert recent_order.filled_quantity == 2.0
        assert recent_order.avg_fill_price == 101.0
        assert recent_order.cumulative_fee_amount == 0.4
        assert len(fills) == 1
        assert fills[0].fill_quantity == 2.0
        assert fills[0].fill_price == 101.0
        assert fills[0].slippage_amount == 2.0
        assert len(lots) == 1
        assert lots[0].cost_basis_price == 101.2
    finally:
        state_store.close()


def test_submit_orders_skips_duplicate_when_pending_exists_in_state(tmp_path) -> None:
    state_store = StateStore(db_path=tmp_path / "state.db")
    trade_client = FakeTradeClient(
        pd.DataFrame(
            {
                "order_id": ["unused"],
                "order_status": ["SUBMITTED"],
                "filled_quantity": [0.0],
            }
        )
    )
    instruction = PaperOrderInstruction(
        symbol="US.AMD",
        side=TrdSide.BUY,
        quantity=1.375,
        price=305.33,
        reason="monthly_top_momentum:US.AMD",
    )

    try:
        state_store.record_order(
            OrderRecord(
                order_id="pending-1",
                symbol="US.AMD",
                side="BUY",
                quantity=1.0,
                price=305.33,
                status="submitted",
                reason="monthly_top_momentum:US.AMD",
                filled_quantity=0.0,
            )
        )

        submitted_count = submit_orders_with_duplicate_guard(
            trade_client,
            [instruction],
            "paper",
            lambda *_args, **_kwargs: None,
            state_store=state_store,
        )

        assert submitted_count == 0
        assert trade_client.submit_calls == 0
    finally:
        state_store.close()


def test_submit_orders_does_not_skip_stale_pending_state_order(tmp_path) -> None:
    state_store = StateStore(db_path=tmp_path / "state.db")
    trade_client = FakeTradeClient(
        pd.DataFrame(
            {
                "order_id": ["new-1"],
                "order_status": ["SUBMITTED"],
                "filled_quantity": [0.0],
            }
        )
    )
    instruction = PaperOrderInstruction(
        symbol="US.AMD",
        side=TrdSide.BUY,
        quantity=1.375,
        price=305.33,
        reason="monthly_top_momentum:US.AMD",
    )

    try:
        stale_submitted_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        state_store.record_order(
            OrderRecord(
                order_id="stale-pending-1",
                symbol="US.AMD",
                side="BUY",
                quantity=1.0,
                price=305.33,
                status="submitted",
                reason="monthly_top_momentum:US.AMD",
                filled_quantity=0.0,
                submitted_at=stale_submitted_at,
            )
        )

        submitted_count = submit_orders_with_duplicate_guard(
            trade_client,
            [instruction],
            "paper",
            lambda *_args, **_kwargs: None,
            state_store=state_store,
        )

        assert submitted_count == 1
        assert trade_client.submit_calls == 1
    finally:
        state_store.close()


def test_submit_orders_skips_buy_orders_below_one_share(tmp_path) -> None:
    state_store = StateStore(db_path=tmp_path / "state.db")
    trade_client = FakeTradeClient(
        pd.DataFrame(
            {
                "order_id": ["unused"],
                "order_status": ["SUBMITTED"],
                "filled_quantity": [0.0],
            }
        )
    )
    instruction = PaperOrderInstruction(
        symbol="US.AMD",
        side=TrdSide.BUY,
        quantity=0.431,
        price=347.81,
        reason="monthly_top_momentum:US.AMD",
    )

    try:
        submitted_count = submit_orders_with_duplicate_guard(
            trade_client,
            [instruction],
            "paper",
            lambda *_args, **_kwargs: None,
            state_store=state_store,
        )

        assert submitted_count == 0
        assert trade_client.submit_calls == 0
    finally:
        state_store.close()


def test_submit_orders_treats_order_rejected_error_as_skippable(tmp_path) -> None:
    state_store = StateStore(db_path=tmp_path / "state.db")
    trade_client = RejectedTradeClient(
        "Failed to submit paper order for US.AVGO: Not enough positions"
    )
    instruction = PaperOrderInstruction(
        symbol="US.AVGO",
        side=TrdSide.BUY,
        quantity=4010.0,
        price=422.76,
        reason="paper_repair:cover_short:US.AVGO",
    )

    try:
        submitted_count = submit_orders_with_duplicate_guard(
            trade_client,
            [instruction],
            "paper",
            lambda *_args, **_kwargs: None,
            state_store=state_store,
        )

        assert submitted_count == 0
        assert trade_client.submit_calls == 1
    finally:
        state_store.close()