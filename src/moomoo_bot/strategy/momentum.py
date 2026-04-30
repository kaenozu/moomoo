"""Momentum rotation strategy module.

Purpose: Cross-sectional and monthly momentum rotation strategies.
Related: strategy/base.py, backtest/engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .base import Strategy, TradeDecision


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
    fallback_asset_symbol: str | None = None
    fallback_allocation: float = 0.0
    inverse_volatility: bool = False
    volatility_lookback_days: int = 22


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

                # Phase 2: Fallback Asset
                fallback_weights: dict[str, float] = {}
                if (
                    self.config.fallback_asset_symbol
                    and self.config.fallback_allocation > 0.0
                ):
                    remaining_weight = max(0.0, 1.0 - sum(preserved.values()))
                    if remaining_weight > 0.0:
                        fb_share = min(
                            remaining_weight, self.config.fallback_allocation
                        )
                        fallback_weights[self.config.fallback_asset_symbol] = fb_share

                self._current_weights = {**preserved, **fallback_weights}
                self._entry_index = {
                    symbol: self._entry_index.get(symbol, len(frame))
                    for symbol in self._current_weights
                }
                reason = "no_symbols_above_trend"
                if fallback_weights:
                    reason += f":fallback_to:{self.config.fallback_asset_symbol}"
            else:
                held_over: dict[str, float] = {}
                if self.config.min_hold_days > 0:
                    for symbol, weight in self._current_weights.items():
                        entry_index = self._entry_index.get(symbol)
                        if (
                            symbol not in selected_symbols
                            and entry_index is not None
                            and len(frame) - entry_index < self.config.min_hold_days
                        ):
                            held_over[symbol] = weight

                remaining_weight = max(0.0, 1.0 - sum(held_over.values()))
                new_symbols = [
                    symbol for symbol in selected_symbols if symbol not in held_over
                ]
                new_weights: dict[str, float] = {}
                if new_symbols and remaining_weight > 0.0:
                    # Phase 3: Inverse Volatility Weighting
                    if self.config.inverse_volatility:
                        vols: dict[str, float] = {}
                        for sym in new_symbols:
                            returns = (
                                frame[sym]
                                .tail(self.config.volatility_lookback_days)
                                .pct_change()
                                .dropna()
                            )
                            vol = returns.std()
                            vols[sym] = vol if vol > 0 else 1.0

                        inv_vols = {sym: 1.0 / v for sym, v in vols.items()}
                        total_inv_vol = sum(inv_vols.values())
                        new_weights = {
                            sym: (inv_vols[sym] / total_inv_vol) * remaining_weight
                            for sym in new_symbols
                        }
                    else:
                        share = remaining_weight / len(new_symbols)
                        new_weights = {symbol: share for symbol in new_symbols}

                self._current_weights = {**held_over, **new_weights}
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

class CoreSatelliteStrategy:
    """Blend an active strategy sleeve with a benchmark core sleeve."""

    def __init__(
        self,
        strategy: Strategy,
        benchmark_symbol: str,
        satellite_weight: float,
    ) -> None:
        if not 0.0 <= satellite_weight <= 1.0:
            raise ValueError("satellite_weight must be between 0.0 and 1.0")
        self.strategy = strategy
        self.benchmark_symbol = benchmark_symbol
        self.satellite_weight = satellite_weight

    @property
    def requires_benchmark_prices(self) -> bool:
        return self.satellite_weight < 1.0

    def reset(self) -> None:
        reset_strategy = getattr(self.strategy, "reset", None)
        if callable(reset_strategy):
            reset_strategy()

    def __getattr__(self, name: str):
        return getattr(self.strategy, name)

    def decide(self, prices: pd.DataFrame, as_of: pd.Timestamp) -> TradeDecision:
        active_prices = prices.drop(columns=[self.benchmark_symbol], errors="ignore")
        decision = self.strategy.decide(active_prices, as_of)
        active_weights = {
            symbol: round(float(weight) * self.satellite_weight, 12)
            for symbol, weight in decision.target_weights.items()
            if symbol != self.benchmark_symbol
        }
        benchmark_weight = round(
            (1.0 - self.satellite_weight)
            + float(decision.target_weights.get(self.benchmark_symbol, 0.0))
            * self.satellite_weight,
            12,
        )
        if benchmark_weight > 0.0:
            active_weights[self.benchmark_symbol] = benchmark_weight

        return TradeDecision(
            as_of=decision.as_of,
            target_weights=active_weights,
            reason=f"{decision.reason}:core_satellite={self.satellite_weight:.0%}/{(1.0 - self.satellite_weight):.0%}",
        )



class DynamicCoreSatelliteStrategy(CoreSatelliteStrategy):
    """CoreSatelliteStrategy with regime-adaptive satellite weight.

    When the benchmark is trending up (price > N-day SMA) the full
    ``satellite_weight_bull`` is used to maximise active exposure.
    When the benchmark falls below its moving average the satellite weight
    drops to ``satellite_weight_bear``, reducing active risk automatically.
    """

    def __init__(
        self,
        strategy: Strategy,
        benchmark_symbol: str,
        satellite_weight_bull: float = 0.45,
        satellite_weight_bear: float = 0.20,
        trend_days: int = 200,
    ) -> None:
        if not 0.0 <= satellite_weight_bull <= 1.0:
            raise ValueError("satellite_weight_bull must be between 0.0 and 1.0")
        if not 0.0 <= satellite_weight_bear <= 1.0:
            raise ValueError("satellite_weight_bear must be between 0.0 and 1.0")
        super().__init__(strategy, benchmark_symbol, satellite_weight_bull)
        self._satellite_weight_bull = satellite_weight_bull
        self._satellite_weight_bear = satellite_weight_bear
        self._trend_days = trend_days

    @property
    def requires_benchmark_prices(self) -> bool:
        return True

    def decide(self, prices: pd.DataFrame, as_of: pd.Timestamp) -> TradeDecision:
        benchmark_series = prices.get(self.benchmark_symbol)
        if benchmark_series is not None:
            bm = benchmark_series.dropna()
            if len(bm) >= self._trend_days:
                ma = float(bm.tail(self._trend_days).mean())
                current_price = float(bm.iloc[-1])
                is_bull = current_price > ma
            else:
                is_bull = True  # not enough history; default to bull
        else:
            is_bull = True

        self.satellite_weight = (
            self._satellite_weight_bull if is_bull else self._satellite_weight_bear
        )
        regime_label = "bull" if is_bull else "bear"
        result = super().decide(prices, as_of)
        return TradeDecision(
            as_of=result.as_of,
            target_weights=result.target_weights,
            reason=f"{result.reason}:regime={regime_label}:sat={self.satellite_weight:.0%}",
        )
