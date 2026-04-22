"""CLI helper utilities module.

Purpose: Helper functions for CLI commands (parse, load, build strategy).
Related: cli.py.
"""

from pathlib import Path

import pandas as pd
from moomoo import Session, TrdEnv

from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.cli_render import console
from moomoo_bot.strategy.momentum import MonthlyMomentumRotationConfig, MonthlyMomentumRotationStrategy


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
    market_state = str(market_state_frame.iloc[0].get("market_state", "")).strip().upper()
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


def build_monthly_strategy(settings, min_hold_days: int | None = None):
    return MonthlyMomentumRotationStrategy(
        MonthlyMomentumRotationConfig(
            lookback_days=settings.lookback_days,
            trend_days=settings.trend_days,
            top_n=settings.top_n,
            skip_days=settings.skip_days,
            rebalance_days=settings.rebalance_days,
            min_hold_days=min_hold_days if min_hold_days is not None else settings.min_hold_days,
            volatility_lookback_days=settings.volatility_lookback_days,
            max_volatility_percentile=settings.max_volatility_percentile,
            relative_strength_lookback_days=settings.relative_strength_lookback_days,
            fallback_asset_symbol=settings.fallback_asset_symbol,
            fallback_allocation=settings.fallback_allocation,
        )
    )


def position_quantities(position_frame: pd.DataFrame) -> dict[str, float]:
    positions: dict[str, float] = {}
    for _, row in position_frame.iterrows():
        code = str(row.get("code", "")).strip()
        if not code:
            continue
        qty = float(row.get("qty", 0.0) or 0.0)
        if qty > 0.0:
            positions[code] = qty
    return positions


def require_paper_mode(settings, command_name: str) -> None:
    if settings.execution_mode != "paper":
        raise ValueError(f"{command_name} requires MOOMOO_BOT_EXECUTION_MODE=paper")


def require_live_mode(settings, command_name: str, confirm_live_trading: bool) -> None:
    if settings.execution_mode != "live":
        raise ValueError(f"{command_name} requires MOOMOO_BOT_EXECUTION_MODE=live")
    if not settings.allow_live_trading:
        raise ValueError(f"{command_name} requires MOOMOO_BOT_ALLOW_LIVE_TRADING=true")
    if not confirm_live_trading:
        raise ValueError(f"{command_name} requires --confirm-live-trading")


def trade_mode_label(trd_env: TrdEnv) -> str:
    return "live" if trd_env == TrdEnv.REAL else "paper"


def select_session(market_open: bool) -> Session:
    return Session.NONE if market_open else Session.ETH


def select_fill_outside_rth(market_open: bool) -> bool:
    return not market_open


def submit_orders_with_duplicate_guard(trade_client, instructions, mode_label: str, render_func) -> None:
    for instruction in instructions:
        matching_order = get_matching_active_order(trade_client, instruction)
        if matching_order is not None:
            console.print(
                f"Skipping duplicate {mode_label} order for {instruction.symbol} qty={instruction.quantity:.3f} "
                f"price={instruction.price:.2f} order_id={matching_order.get('order_id')} "
                f"status={matching_order.get('order_status')}"
            )
            continue

        response = trade_client.submit_order(instruction)
        render_func(instruction, response)


def get_matching_active_order(trade_client, instruction):
    matcher = getattr(trade_client, "get_matching_active_order", None)
    if matcher is None:
        return None
    return matcher(instruction, refresh_cache=True)
