from __future__ import annotations

import pandas as pd

from moomoo_bot.orchestrator.helpers import (
    effective_max_position_weight,
    market_date_for_frame,
    overlay_latest_prices,
    resolve_order_prices,
    signed_position_quantities,
    snapshot_latest_prices,
)
from moomoo_bot.risk import RiskState


class FakeQuoteClient:
    def __init__(self, snapshot: pd.DataFrame | Exception) -> None:
        self.snapshot = snapshot
        self.calls: list[list[str]] = []

    def fetch_market_snapshot(self, symbol_universe: list[str]) -> pd.DataFrame:
        self.calls.append(list(symbol_universe))
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot.copy()


def test_resolve_order_prices_overlays_positive_snapshot_prices() -> None:
    quote_client = FakeQuoteClient(
        pd.DataFrame(
            {
                "code": ["US.AAPL", "US.MSFT", "", "US.NVDA"],
                "last_price": [105.555, 0.0, 999.0, "bad"],
            }
        )
    )

    resolved = resolve_order_prices(
        quote_client,
        ["US.AAPL", "US.MSFT", "US.NVDA"],
        {"US.AAPL": 101.0, "US.MSFT": 202.0, "US.NVDA": 303.0},
    )

    assert quote_client.calls == [["US.AAPL", "US.MSFT", "US.NVDA"]]
    assert resolved == {"US.AAPL": 105.56, "US.MSFT": 202.0, "US.NVDA": 303.0}


def test_resolve_order_prices_falls_back_when_snapshot_fetch_fails() -> None:
    fallback_prices = {"US.AAPL": 101.0}

    resolved = resolve_order_prices(
        FakeQuoteClient(RuntimeError("snapshot unavailable")),
        ["US.AAPL"],
        fallback_prices,
    )

    assert resolved == fallback_prices


def test_snapshot_latest_prices_returns_only_positive_rounded_prices() -> None:
    latest_prices = snapshot_latest_prices(
        FakeQuoteClient(
            pd.DataFrame(
                {
                    "code": ["US.AAPL", "US.MSFT", "US.NVDA"],
                    "last_price": [105.555, None, -1.0],
                }
            )
        ),
        ["US.AAPL", "US.MSFT", "US.NVDA"],
    )

    assert latest_prices == {"US.AAPL": 105.56}


def test_overlay_latest_prices_updates_last_row_without_mutating_input() -> None:
    original = pd.DataFrame(
        {"US.AAPL": [100.0, 101.0], "US.MSFT": [200.0, 201.0]},
        index=pd.to_datetime(["2025-01-02", "2025-01-03"]),
    )

    adjusted = overlay_latest_prices(original, {"US.AAPL": 105.56, "US.NVDA": 300.0})

    assert adjusted.loc[pd.Timestamp("2025-01-03"), "US.AAPL"] == 105.56
    assert adjusted.loc[pd.Timestamp("2025-01-03"), "US.MSFT"] == 201.0
    assert original.loc[pd.Timestamp("2025-01-03"), "US.AAPL"] == 101.0


def test_signed_position_quantities_reads_alias_columns_and_preserves_shorts() -> None:
    position_frame = pd.DataFrame(
        [
            {"stock_code": "US.AAPL", "position_qty": 3.5},
            {"ticker": "US.TSLA", "holding_qty": -1.25},
            {"code": "US.MSFT", "can_use_qty": 0.0},
            {"code": "", "qty": 2.0},
        ]
    )

    positions = signed_position_quantities(position_frame)

    assert positions == {"US.AAPL": 3.5, "US.TSLA": -1.25}


def test_effective_max_position_weight_halves_only_at_active_drawdown_tier() -> None:
    assert effective_max_position_weight(0.4, RiskState(drawdown_tier=0)) == 0.4
    assert effective_max_position_weight(0.4, RiskState(drawdown_tier=1)) == 0.2
    assert (
        effective_max_position_weight(0.4, RiskState(halted=True, drawdown_tier=2))
        == 0.4
    )


def test_market_date_for_frame_accepts_string_index_values() -> None:
    price_frame = pd.DataFrame(
        {"US.AAPL": [100.0]},
        index=pd.Index(["2025-01-03T00:00:00+00:00"]),
    )

    assert market_date_for_frame(price_frame) == "2025-01-03"
