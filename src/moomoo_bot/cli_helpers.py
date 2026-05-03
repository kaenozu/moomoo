"""CLI helper utilities module.

Purpose: Helper functions for CLI commands (parse, load, build strategy).
Why: Keeps CLI parsing and strategy construction separate from order submission.
Related: cli.py, order_submission.py, strategy/momentum.py.
"""

from pathlib import Path

import logging
import pandas as pd
from moomoo import TrdEnv

from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.order_submission import (  # noqa: F401
    get_matching_active_order,
    submit_orders_with_duplicate_guard,
)
from moomoo_bot.row_utils import position_quantities_from_frame
from moomoo_bot.strategy.momentum import (
    CoreSatelliteStrategy,
    MonthlyMomentumRotationConfig,
    MonthlyMomentumRotationStrategy,
)

logger = logging.getLogger(__name__)


def parse_symbols(raw_symbols: str | None) -> list[str]:
    if not raw_symbols:
        return []
    return [symbol.strip() for symbol in raw_symbols.split(",") if symbol.strip()]


def parse_weights(raw_weights: str | None) -> list[float]:
    if not raw_weights:
        return []
    weights: list[float] = []
    for raw_weight in raw_weights.split(","):
        raw_weight = raw_weight.strip()
        if not raw_weight:
            continue
        weight = float(raw_weight)
        if not 0.0 <= weight <= 1.0:
            raise ValueError("satellite weights must be between 0 and 1.")
        weights.append(weight)
    return weights


def fetch_market_state(client: MoomooOpenDClient, benchmark_symbol: str) -> str:
    market_state_frame = client.fetch_market_state([benchmark_symbol])
    if market_state_frame.empty:
        raise RuntimeError(f"No market state returned for {benchmark_symbol}")
    if len(market_state_frame) > 1:
        logger.warning(
            "Multiple market state rows returned for %s, using first",
            benchmark_symbol,
        )
    market_state = (
        str(market_state_frame.iloc[0].get("market_state", "")).strip().upper()
    )
    if not market_state:
        raise RuntimeError(f"Market state returned no value for {benchmark_symbol}")
    return market_state


def is_regular_market_open(market_state: str) -> bool:
    return market_state in {"MORNING", "AFTERNOON"}


def load_price_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    return frame.apply(pd.to_numeric, errors="coerce").dropna(how="all")


def load_benchmark_series(path: Path) -> pd.Series:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    if frame.shape[1] != 1:
        raise ValueError("benchmark_csv must contain exactly one column.")
    series = frame.iloc[:, 0].apply(pd.to_numeric, errors="coerce").dropna()
    series.name = "benchmark"
    return series


def build_monthly_strategy(
    settings,
    min_hold_days: int | None = None,
    satellite_weight: float | None = None,
    inverse_volatility: bool = False,
):
    active_strategy = MonthlyMomentumRotationStrategy(
        MonthlyMomentumRotationConfig(
            lookback_days=settings.lookback_days,
            trend_days=settings.trend_days,
            top_n=settings.top_n,
            skip_days=settings.skip_days,
            rebalance_days=settings.rebalance_days,
            min_hold_days=min_hold_days
            if min_hold_days is not None
            else settings.min_hold_days,
            inverse_volatility=inverse_volatility,
            fallback_asset_symbol=settings.fallback_asset_symbol,
            fallback_allocation=settings.fallback_allocation,
            volatility_lookback_days=settings.volatility_lookback_days,
        )
    )

    resolved_satellite_weight = (
        satellite_weight if satellite_weight is not None else settings.satellite_weight
    )
    return CoreSatelliteStrategy(
        active_strategy,
        benchmark_symbol=settings.benchmark_symbol,
        satellite_weight=resolved_satellite_weight,
    )


def requires_benchmark_prices(strategy) -> bool:
    return bool(getattr(strategy, "requires_benchmark_prices", False))


def position_quantities(position_frame: pd.DataFrame) -> dict[str, float]:
    return position_quantities_from_frame(position_frame)


def trade_mode_label(trd_env: TrdEnv) -> str:
    return "live" if trd_env == TrdEnv.REAL else "paper"
