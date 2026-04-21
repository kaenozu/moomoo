"""Risk management module.

Purpose: Handle risk detection, stop-loss, take-profit, and position liquidation.
Related: cli.py, paper.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd
from moomoo import Session, TrdSide

from moomoo_bot.paper import PaperOrderInstruction, normalize_order_quantity


@dataclass
class RiskState:
    peak_account_value: float | None = None
    halted: bool = False
    halted_reason: str | None = None


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


def update_drawdown_state(account_value: float, state: RiskState, max_drawdown_pct: float) -> str | None:
    if account_value <= 0.0:
        account_value = 0.0

    if state.peak_account_value is None or account_value > state.peak_account_value:
        state.peak_account_value = account_value
        return None

    peak = state.peak_account_value
    if peak is None or peak <= 0.0:
        return None

    drawdown_pct = (peak - account_value) / peak
    if max_drawdown_pct > 0.0 and drawdown_pct >= max_drawdown_pct:
        state.halted = True
        state.halted_reason = (
            f"max_drawdown: equity fell {drawdown_pct:.2%} from peak {peak:.2f} to {account_value:.2f} "
            f"(threshold {-max_drawdown_pct:.2%})"
        )
        return state.halted_reason
    return None


def build_liquidation_orders(
    positions: Mapping[str, float],
    latest_prices: Mapping[str, float],
    reason: str,
    session: Session = Session.NONE,
    fill_outside_rth: bool = False,
) -> list[PaperOrderInstruction]:
    orders: list[PaperOrderInstruction] = []
    for symbol, quantity in positions.items():
        sell_qty = normalize_order_quantity(quantity)
        if sell_qty <= 0.0:
            continue
        if symbol not in latest_prices:
            raise ValueError(f"missing latest price for liquidation symbol {symbol}")
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
) -> list[PaperOrderInstruction]:
    if position_rows.empty:
        return []

    orders: list[PaperOrderInstruction] = []
    for _, row in position_rows.iterrows():
        symbol = _extract_symbol(row)
        if not symbol or symbol not in latest_prices:
            continue

        quantity = _extract_float(row, ("qty", "position_qty", "holding_qty", "can_use_qty"))
        if quantity is None or quantity <= 0.0:
            continue
        quantity = normalize_order_quantity(quantity)
        if quantity <= 0.0:
            continue

        basis = _extract_float(row, ("cost_price", "avg_cost", "avg_price", "price_cost", "cost"))
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


def _extract_symbol(row: pd.Series) -> str:
    for field in ("code", "symbol", "stock_code", "ticker"):
        value = row.get(field)
        if value is None or pd.isna(value):
            continue
        symbol = str(value).strip()
        if symbol and symbol.lower() != "nan":
            return symbol
    return ""


def _extract_float(row: pd.Series, fields: tuple[str, ...]) -> float | None:
    for field in fields:
        value = row.get(field)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0.0:
            return numeric
    return None