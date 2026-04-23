"""Momentum rotation strategy module.

Purpose: Cross-sectional and monthly momentum rotation strategies.
Related: strategy/base.py, backtest/engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .base import TradeDecision


@dataclass(frozen=True)
class MomentumRotationConfig:
    lookback_days: int = 63
    trend_days: int = 200
    top_n: int = 3


class MomentumRotationStrategy:
    """Cross-sectional momentum strategy with a trend filter."""

    def __init__(self, config: MomentumRotationConfig | None = None) -> None:
        self.config = config or MomentumRotationConfig()

    def decide(self, prices: pd.DataFrame, as_of: pd.Timestamp) -> TradeDecision:
        frame = prices.loc[:as_of].dropna(how="all")
        required_rows = max(self.config.lookback_days, self.config.trend_days) + 1
        if len(frame) < required_rows:
            return TradeDecision(
                as_of=as_of, target_weights={}, reason="insufficient_history"
            )

        latest = frame.iloc[-1]
        past = frame.iloc[-(self.config.lookback_days + 1)]
        trend = frame.tail(self.config.trend_days).mean()

        momentum = latest.div(past).sub(1.0)
        eligible = momentum[(latest > trend) & momentum.notna()].sort_values(
            ascending=False
        )
        selected = eligible.head(self.config.top_n)

        if selected.empty:
            return TradeDecision(
                as_of=as_of, target_weights={}, reason="no_symbols_above_trend"
            )

        weight = 1.0 / len(selected)
        target_weights = {symbol: weight for symbol in selected.index}
        return TradeDecision(
            as_of=as_of,
            target_weights=target_weights,
            reason=f"top_momentum:{','.join(selected.index.tolist())}",
        )


@dataclass(frozen=True)
class MonthlyMomentumRotationConfig:
    lookback_days: int = 252
    trend_days: int = 252
    top_n: int = 1
    skip_days: int = 21
    rebalance_days: int = 21
    min_hold_days: int = 0


class MonthlyMomentumRotationStrategy:
    """Monthly cross-sectional momentum strategy with a skip-month filter.

    This is the strategy family that performed best in the real-data search:
    long-only, equal-weighted, equal-weighted, monthly rebalanced, 12-month
    lookback, 12-month trend filter, and a 1-month skip.

    NOTE: This strategy is stateful. Each call to ``decide`` updates internal
    tracking of current holdings, entry indices, and the last rebalance point.
    Call ``reset()`` before reusing the same instance for a fresh backtest run,
    or create a new instance for each independent run.
    """

    def __init__(self, config: MonthlyMomentumRotationConfig | None = None) -> None:
        self.config = config or MonthlyMomentumRotationConfig()
        self._current_weights: dict[str, float] = {}
        self._entry_index: dict[str, int] = {}
        self._last_rebalance_length = -1

    def reset(self) -> None:
        self._current_weights = {}
        self._entry_index = {}
        self._last_rebalance_length = -1

    def decide(self, prices: pd.DataFrame, as_of: pd.Timestamp) -> TradeDecision:
        frame = prices.loc[:as_of].dropna(how="all")
        required_rows = max(
            self.config.lookback_days + self.config.skip_days + 1,
            self.config.trend_days + 1,
        )
        if len(frame) < required_rows:
            return TradeDecision(
                as_of=as_of, target_weights={}, reason="insufficient_history"
            )

        should_rebalance = (
            self._last_rebalance_length < 0
            or (len(frame) - required_rows) % self.config.rebalance_days == 0
        )
        if should_rebalance:
            latest = frame.iloc[-1]
            trend = frame.tail(self.config.trend_days).mean()
            reference = frame.iloc[
                -(self.config.lookback_days + self.config.skip_days + 1)
            ]
            momentum = latest.div(reference).sub(1.0)

            eligible = momentum[(latest > trend) & momentum.notna()].sort_values(
                ascending=False
            )
            selected_symbols = eligible.head(self.config.top_n).index.tolist()

            if not selected_symbols:
                preserved = {}
                if self.config.min_hold_days > 0:
                    for symbol, weight in self._current_weights.items():
                        entry_index = self._entry_index.get(symbol)
                        if (
                            entry_index is not None
                            and len(frame) - entry_index < self.config.min_hold_days
                        ):
                            preserved[symbol] = weight
                self._current_weights = preserved
                self._entry_index = {
                    symbol: self._entry_index[symbol] for symbol in preserved
                }
                reason = "no_symbols_above_trend"
            else:
                preserved: dict[str, float] = {}
                if self.config.min_hold_days > 0:
                    for symbol, weight in self._current_weights.items():
                        entry_index = self._entry_index.get(symbol)
                        if (
                            symbol not in selected_symbols
                            and entry_index is not None
                            and len(frame) - entry_index < self.config.min_hold_days
                        ):
                            preserved[symbol] = weight

                remaining_weight = max(0.0, 1.0 - sum(preserved.values()))
                new_symbols = [
                    symbol for symbol in selected_symbols if symbol not in preserved
                ]
                new_weights: dict[str, float] = {}
                if new_symbols and remaining_weight > 0.0:
                    share = remaining_weight / len(new_symbols)
                    new_weights = {symbol: share for symbol in new_symbols}

                self._current_weights = {**preserved, **new_weights}
                self._entry_index = {
                    symbol: self._entry_index.get(symbol, len(frame))
                    for symbol in self._current_weights
                }
                for symbol in new_weights:
                    self._entry_index[symbol] = len(frame)

                reason = f"monthly_top_momentum:{','.join(selected_symbols)}"

            self._last_rebalance_length = len(frame)
        else:
            reason = "hold"

        return TradeDecision(
            as_of=as_of, target_weights=self._current_weights, reason=reason
        )
