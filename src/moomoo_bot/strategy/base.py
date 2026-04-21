"""Strategy base module.

Purpose: Define Strategy protocol and TradeDecision.
Related: strategy/__init__.py, backtest/engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class TradeDecision:
    as_of: pd.Timestamp
    target_weights: dict[str, float]
    reason: str


class Strategy(Protocol):
    def decide(self, prices: pd.DataFrame, as_of: pd.Timestamp) -> TradeDecision:
        """Return the target portfolio weights for the next rebalance."""
