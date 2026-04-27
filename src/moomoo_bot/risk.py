"""Risk management module.

Purpose: Handle risk detection, stop-loss, take-profit, and position liquidation.
Related: cli.py, paper.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import logging

import pandas as pd
from moomoo import Session, TrdSide

from moomoo_bot.paper import PaperOrderInstruction
from moomoo_bot.quantities import round_quantity_toward_zero
from moomoo_bot.row_utils import row_text, row_float
from moomoo_bot.state import TaxLotRealizationRecord

logger = logging.getLogger(__name__)

_DRAWDOWN_TIER1_THRESHOLD = 0.66


@dataclass
class RiskState:
    peak_account_value: float | None = None
    halted: bool = False
    halted_reason: str | None = None
    drawdown_tier: int = 0


def detect_market_shock(benchmark_series: pd.Series, drop_pct: float) -> str | None:
    if drop_pct <= 0.0:
        return None

    series = pd.to_numeric(benchmark_series, errors="coerce").dropna()
    if len(series) < 2:
        return None

    previous_close = float(series.iloc[-2])
    latest_close = float(series.iloc[-1])
    if previous_close <= 0.0:
        return None

    change_pct = latest_close / previous_close - 1.0
    if change_pct <= -drop_pct:
        return (
            f"market_shock: benchmark dropped {change_pct:.2%} from {previous_close:.2f} to {latest_close:.2f} "
            f"(threshold {-drop_pct:.2%})"
        )
    return None


def update_drawdown_state(
    account_value: float,
    state: RiskState,
    max_drawdown_pct: float,
    max_drawdown_reset_pct: float = 0.0,
) -> str | None:
    """Update drawdown state and manage tiers (Phase 2).

    Mutates state in place and returns halt reason if any.
    """
    if account_value <= 0.0:
        account_value = 0.0

    # New peak found
    if state.peak_account_value is None or account_value > state.peak_account_value:
        state.peak_account_value = account_value
        state.halted = False
        state.halted_reason = None
        state.drawdown_tier = 0
        return None

    peak = state.peak_account_value
    if peak <= 0.0:
        return None

    drawdown_pct = (peak - account_value) / peak

    # Recovery logic
    if state.halted:
        # If recovered within reset threshold, unhalt
        if max_drawdown_reset_pct > 0.0 and account_value >= peak * (
            1.0 - max_drawdown_reset_pct
        ):
            state.halted = False
            state.halted_reason = None
            state.drawdown_tier = 0
            return None
        return state.halted_reason

    # Gradual de-risking logic (Tiered drawdown)
    # Tier 0 -> Tier 1 (50% reduction) at 2/3 of max_drawdown_pct
    tier1_threshold = max_drawdown_pct * _DRAWDOWN_TIER1_THRESHOLD
    if state.drawdown_tier == 0 and drawdown_pct >= tier1_threshold:
        state.drawdown_tier = 1
        # We don't halt here, just signal to orchestrator to reduce positions

    # Full halt at max_drawdown_pct
    if drawdown_pct >= max_drawdown_pct:
        state.halted = True
        state.drawdown_tier = 2
        state.halted_reason = f"max_drawdown:{drawdown_pct:.2%} from {peak:.0f}"
        return state.halted_reason

    return None


def detect_daily_loss_limit(
    last_equity: float | None, current_equity: float, limit_pct: float
) -> str | None:
    """Detect if daily loss limit was breached."""
    if last_equity is None or last_equity <= 0.0 or limit_pct <= 0.0:
        return None

    loss_pct = (last_equity - current_equity) / last_equity
    if loss_pct >= limit_pct:
        return f"daily_loss_limit:{loss_pct:.2%} limit:{limit_pct:.2%}"
    return None


def detect_monthly_loss_limit(
    month_start_equity: float | None,
    current_equity: float,
    limit_pct: float,
) -> str | None:
    """Detect if the monthly loss limit has been breached."""
    if month_start_equity is None or month_start_equity <= 0.0 or limit_pct <= 0.0:
        return None
    loss_pct = (month_start_equity - current_equity) / month_start_equity
    if loss_pct >= limit_pct:
        return f"monthly_loss_limit:{loss_pct:.2%} limit:{limit_pct:.2%}"
    return None


def detect_low_ev_condition(
    realizations: list[TaxLotRealizationRecord],
    lookback_n: int,
    halt_threshold: float,
    reduce_threshold: float,
) -> tuple[str | None, bool]:
    """Evaluate rolling expected value over the last lookback_n closed trades.

    Returns (halt_reason, should_reduce):
    - halt_reason: non-None if EV ratio < halt_threshold → system halts
    - should_reduce: True if EV ratio < reduce_threshold but >= halt_threshold → half-size

    EV ratio = (avg realized PnL per trade) / (avg opening price).
    Defaults of 0.0 for both thresholds mean: halt only on negative average PnL,
    no intermediate reduction step.
    """
    if not realizations or lookback_n <= 0:
        return None, False

    recent = realizations[:lookback_n]
    pnls = [float(r.realized_pnl) for r in recent if r.realized_pnl is not None]
    if len(pnls) < max(1, lookback_n // 2):
        # Not enough data to make a reliable assessment.
        return None, False

    avg_pnl = sum(pnls) / len(pnls)
    opening_prices = [
        float(r.opening_price)
        for r in recent
        if r.opening_price is not None and float(r.opening_price) > 0
    ]
    avg_opening_price = (
        sum(opening_prices) / len(opening_prices) if opening_prices else 1.0
    )
    ev_ratio = avg_pnl / avg_opening_price if avg_opening_price > 0 else avg_pnl

    if ev_ratio < halt_threshold:
        return (
            f"low_ev:{ev_ratio:.4f}<{halt_threshold:.4f} over last {len(pnls)} trades",
            False,
        )
    if reduce_threshold > halt_threshold and ev_ratio < reduce_threshold:
        return None, True
    return None, False


def calculate_volatility_scalar(
    prices: pd.Series, lookback: int, target_vol: float
) -> float:
    """Calculate position scalar based on target volatility."""
    if lookback <= 1 or target_vol <= 0.0 or len(prices) < lookback:
        return 1.0

    returns = prices.pct_change().dropna().iloc[-lookback:]
    if returns.empty:
        return 1.0

    realized_vol = returns.std() * (252**0.5)
    if realized_vol <= 0.0:
        return 1.0

    scalar = target_vol / realized_vol
    return min(1.0, scalar)


def build_liquidation_orders(
    positions: Mapping[str, float],
    latest_prices: Mapping[str, float],
    reason: str,
    session: Session = Session.NONE,
    fill_outside_rth: bool = False,
    fractional_share_precision: float = 1000.0,
) -> list[PaperOrderInstruction]:
    orders: list[PaperOrderInstruction] = []
    for symbol, quantity in positions.items():
        sell_qty = round_quantity_toward_zero(
            quantity, precision=fractional_share_precision
        )
        if sell_qty <= 0.0:
            continue
        if symbol not in latest_prices:
            logger.warning("No price for %s, skipping liquidation", symbol)
            continue
        orders.append(
            PaperOrderInstruction(
                symbol=symbol,
                side=TrdSide.SELL,
                quantity=sell_qty,
                price=float(latest_prices[symbol]),
                reason=reason,
                session=session,
                fill_outside_rth=fill_outside_rth,
            )
        )
    return sorted(orders, key=lambda instruction: instruction.symbol)


def build_stop_loss_take_profit_orders(
    position_rows: pd.DataFrame,
    latest_prices: Mapping[str, float],
    stop_loss_pct: float,
    take_profit_pct: float,
    session: Session = Session.NONE,
    fill_outside_rth: bool = False,
    fractional_share_precision: float = 1000.0,
) -> list[PaperOrderInstruction]:
    if position_rows.empty:
        return []

    orders: list[PaperOrderInstruction] = []
    for _, row in position_rows.iterrows():
        symbol = row_text(row, "code", "symbol", "stock_code", "ticker")
        if not symbol or symbol not in latest_prices:
            continue

        quantity = row_float(row, "qty", "position_qty", "holding_qty", "can_use_qty")
        if quantity is None or quantity <= 0.0:
            continue
        quantity = round_quantity_toward_zero(
            quantity, precision=fractional_share_precision
        )
        if quantity <= 0.0:
            continue

        basis = row_float(
            row, "cost_price", "avg_cost", "avg_price", "price_cost", "cost"
        )
        if basis is None or basis <= 0.0:
            continue

        latest_price = float(latest_prices[symbol])
        if stop_loss_pct > 0.0 and latest_price <= basis * (1.0 - stop_loss_pct):
            orders.append(
                PaperOrderInstruction(
                    symbol=symbol,
                    side=TrdSide.SELL,
                    quantity=quantity,
                    price=latest_price,
                    reason=f"risk:stop_loss:{symbol}:{latest_price:.2f}<={basis:.2f}",
                    session=session,
                    fill_outside_rth=fill_outside_rth,
                )
            )
            continue

        if take_profit_pct > 0.0 and latest_price >= basis * (1.0 + take_profit_pct):
            orders.append(
                PaperOrderInstruction(
                    symbol=symbol,
                    side=TrdSide.SELL,
                    quantity=quantity,
                    price=latest_price,
                    reason=f"risk:take_profit:{symbol}:{latest_price:.2f}>={basis:.2f}",
                    session=session,
                    fill_outside_rth=fill_outside_rth,
                )
            )

    return sorted(orders, key=lambda instruction: instruction.symbol)
