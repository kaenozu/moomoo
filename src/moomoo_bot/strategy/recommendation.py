"""Recommendation strategy module.

Purpose: Generate stock recommendations based on technical and fundamental criteria.
Related: strategy/base.py, broker/opend.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from .base import TradeDecision

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecommendationConfig:
    symbols: list[str] = field(default_factory=list)
    top_n: int = 5
    min_market_cap: float = 0.0
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    ma_short: int = 20
    ma_long: int = 50
    min_volume: float = 0.0
    weighting_method: str = "equal"
    benchmark_symbol: str = "US.SPY"


class RecommendationStrategy:
    """Generate stock recommendations based on technical analysis."""

    def __init__(self, config: RecommendationConfig | None = None) -> None:
        self.config = config or RecommendationConfig()
        self._quote_client: object | None = None

    def _get_quote_client(self) -> object:
        if self._quote_client is None:
            # Lazy import to avoid circular dependency
            from ..broker.opend import MoomooOpenDClient

            self._quote_client = MoomooOpenDClient()
        return self._quote_client

    def close(self) -> None:
        if self._quote_client is not None:
            self._quote_client.close()
            self._quote_client = None

    def __enter__(self) -> RecommendationStrategy:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0

        delta = prices.diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()

        if loss.iloc[-1] == 0:
            return 100.0

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

    def _calculate_momentum_score(self, symbol: str, history: pd.DataFrame) -> float:
        if history.empty or "close" not in history.columns:
            return 0.0

        close_prices = history["close"]

        roc_5 = (
            ((close_prices.iloc[-1] / close_prices.iloc[-5]) - 1) * 100
            if len(close_prices) >= 5
            else 0
        )
        roc_20 = (
            ((close_prices.iloc[-1] / close_prices.iloc[-20]) - 1) * 100
            if len(close_prices) >= 20
            else 0
        )

        rsi = self._calculate_rsi(close_prices, self.config.rsi_period)

        ma_short = (
            close_prices.rolling(self.config.ma_short).mean().iloc[-1]
            if len(close_prices) >= self.config.ma_short
            else close_prices.iloc[-1]
        )
        ma_long = (
            close_prices.rolling(self.config.ma_long).mean().iloc[-1]
            if len(close_prices) >= self.config.ma_long
            else close_prices.iloc[-1]
        )
        ma_trend = (ma_short - ma_long) / ma_long * 100 if ma_long != 0 else 0

        volume_score = 0.0
        if "volume" in history.columns and len(history) >= 20:
            recent_volume = history["volume"].iloc[-5:].mean()
            past_volume = history["volume"].iloc[-20:-5].mean()
            if past_volume > 0:
                volume_score = ((recent_volume / past_volume) - 1) * 100

        momentum_score = (
            roc_5 * 0.3
            + roc_20 * 0.3
            + (50 - abs(rsi - 50)) * 0.2
            + ma_trend * 0.1
            + volume_score * 0.1
        )

        return max(0.0, momentum_score)

    def _filter_and_score_symbols(
        self, price_frame: pd.DataFrame, symbols: list[str]
    ) -> dict[str, float]:
        scores: dict[str, float] = {}

        for symbol in symbols:
            try:
                if symbol not in price_frame.columns:
                    continue

                prices = price_frame[symbol].dropna()
                if len(prices) < max(self.config.ma_short, self.config.ma_long) + 1:
                    continue

                history = pd.DataFrame({"close": prices})
                score = self._calculate_momentum_score(symbol, history)
                if score > 0:
                    scores[symbol] = score

            except (KeyError, ValueError, TypeError, ZeroDivisionError):
                logger.warning("Failed to score symbol %s", symbol, exc_info=True)
                continue

        return scores

    def decide(self, prices: pd.DataFrame, as_of: pd.Timestamp) -> TradeDecision:
        try:
            symbols_to_analyze = self.config.symbols
            if not symbols_to_analyze:
                symbols_to_analyze = [
                    "US.AAPL",
                    "US.MSFT",
                    "US.GOOGL",
                    "US.AMZN",
                    "US.TSLA",
                    "US.META",
                    "US.NVDA",
                    "US.NFLX",
                ]

            available_symbols = [s for s in symbols_to_analyze if s in prices.columns]
            if not available_symbols:
                return TradeDecision(
                    as_of=as_of, target_weights={}, reason="no_valid_symbols"
                )

            scores = self._filter_and_score_symbols(prices, available_symbols)

            if not scores:
                return TradeDecision(
                    as_of=as_of, target_weights={}, reason="no_scores_calculated"
                )

            sorted_symbols = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            top_symbols = dict(sorted_symbols[: self.config.top_n])

            if not top_symbols:
                return TradeDecision(
                    as_of=as_of, target_weights={}, reason="no_top_symbols"
                )

            target_weights: dict[str, float] = {}

            if self.config.weighting_method == "score":
                total_score = sum(top_symbols.values())
                if total_score > 0:
                    target_weights = {
                        symbol: score / total_score
                        for symbol, score in top_symbols.items()
                    }
                else:
                    weight = 1.0 / len(top_symbols)
                    target_weights = {symbol: weight for symbol in top_symbols}
            else:
                weight = 1.0 / len(top_symbols)
                target_weights = {symbol: weight for symbol in top_symbols}

            reason = f"recommendations:{','.join(top_symbols)}"

            return TradeDecision(
                as_of=as_of, target_weights=target_weights, reason=reason
            )

        except Exception as exc:
            logger.error("Recommendation strategy failed: %s", exc, exc_info=True)
            return TradeDecision(
                as_of=as_of,
                target_weights={},
                reason=f"error:{str(exc)[:50]}",
            )
