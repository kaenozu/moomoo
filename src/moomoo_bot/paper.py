"""Paper trading allocation and order instruction module.

Purpose: Build paper trade plan and convert decisions to order instructions.
Related: cli.py, strategy modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from collections.abc import Mapping

import pandas as pd
from moomoo import Session, TrdSide

from moomoo_bot.strategy.base import TradeDecision


@dataclass(frozen=True)
class PaperAllocation:
    symbol: str
    weight: float
    price: float
    target_value: float
    target_quantity: float
    target_cost: float


@dataclass(frozen=True)
class PaperPlan:
    as_of: pd.Timestamp
    capital: float
    reason: str
    allocations: list[PaperAllocation]
    cash_remaining: float


@dataclass(frozen=True)
class PaperOrderInstruction:
    symbol: str
    side: TrdSide
    quantity: float
    price: float
    reason: str
    session: Session | None = None
    fill_outside_rth: bool = False


def build_paper_plan(
    prices: pd.DataFrame,
    decision: TradeDecision,
    capital: float,
    minimum_order_value: float = 5.0,
    max_position_weight: float = 1.0,
) -> PaperPlan:
    if prices.empty:
        raise ValueError("prices must not be empty")
    if capital <= 0.0:
        raise ValueError("capital must be positive")
    if minimum_order_value < 0.0:
        raise ValueError("minimum_order_value must not be negative")
    if not 0.0 < max_position_weight <= 1.0:
        raise ValueError("max_position_weight must be between 0 and 1")

    latest_prices = prices.loc[:decision.as_of].iloc[-1]
    allocations: list[PaperAllocation] = []
    allocated_cost = 0.0

    total_allocated_weight = 0.0
    pending_allocations: list[tuple[str, float, float, float]] = []

    for symbol, weight in sorted(decision.target_weights.items(), key=lambda item: (-item[1], item[0])):
        if symbol not in latest_prices.index:
            raise ValueError(f"missing latest price for {symbol}")

        price = float(latest_prices[symbol])
        if price <= 0.0:
            raise ValueError(f"invalid price for {symbol}: {price}")

        capped_weight = min(weight, max_position_weight)
        target_value = capital * capped_weight
        target_quantity = floor((target_value / price) * 1000.0) / 1000.0
        target_cost = target_quantity * price

        pending_allocations.append((symbol, capped_weight, price, target_quantity))
        total_allocated_weight += capped_weight

    excess_weight = 1.0 - total_allocated_weight
    if excess_weight > 0.01 and pending_allocations:
        dist_per_symbol = excess_weight / len(pending_allocations)
        reallocated = []
        for symbol, orig_weight, price, qty in pending_allocations:
            new_weight = orig_weight + dist_per_symbol
            new_value = capital * new_weight
            new_qty = floor((new_value / price) * 1000.0) / 1000.0
            new_cost = new_qty * price
            if new_cost >= minimum_order_value:
                reallocated.append((symbol, new_weight, price, new_qty))
                allocated_cost += new_cost

        for symbol, weight, price, quantity in reallocated:
            allocations.append(
                PaperAllocation(
                    symbol=symbol,
                    weight=weight,
                    price=price,
                    target_value=capital * weight,
                    target_quantity=quantity,
                    target_cost=quantity * price,
                )
            )
    else:
        for symbol, weight, price, quantity in pending_allocations:
            target_cost = quantity * price
            if target_cost >= minimum_order_value:
                allocated_cost += target_cost
                allocations.append(
                    PaperAllocation(
                        symbol=symbol,
                        weight=weight,
                        price=price,
                        target_value=capital * weight,
                        target_quantity=quantity,
                        target_cost=target_cost,
                    )
                )

    cash_remaining = capital - allocated_cost
    return PaperPlan(
        as_of=decision.as_of,
        capital=capital,
        reason=decision.reason,
        allocations=allocations,
        cash_remaining=cash_remaining,
    )


def build_paper_rebalance_orders(
    plan: PaperPlan,
    current_positions: Mapping[str, float] | None = None,
    latest_prices: Mapping[str, float] | None = None,
    market_open: bool = True,
) -> list[PaperOrderInstruction]:
    positions = current_positions or {}
    prices = latest_prices or {}
    target_by_symbol = {allocation.symbol: allocation for allocation in plan.allocations}
    instructions: list[PaperOrderInstruction] = []

    for allocation in plan.allocations:
        current_qty = float(positions.get(allocation.symbol, 0.0))
        delta = allocation.target_quantity - current_qty
        if delta > 0.001:
            instructions.append(
                PaperOrderInstruction(
                    symbol=allocation.symbol,
                    side=TrdSide.BUY,
                    quantity=delta,
                    price=allocation.price,
                    reason=plan.reason,
                    session=Session.NONE if market_open else Session.ETH,
                    fill_outside_rth=not market_open,
                )
            )
        elif delta < -0.001:
            instructions.append(
                PaperOrderInstruction(
                    symbol=allocation.symbol,
                    side=TrdSide.SELL,
                    quantity=-delta,
                    price=allocation.price,
                    reason=plan.reason,
                    session=Session.NONE if market_open else Session.ETH,
                    fill_outside_rth=not market_open,
                )
            )

    for symbol, current_qty in positions.items():
        if symbol in target_by_symbol:
            continue
        sell_qty = float(current_qty)
        if sell_qty > 0.001:
            if symbol not in prices:
                raise ValueError(f"missing latest price for liquidation symbol {symbol}")
            instructions.append(
                PaperOrderInstruction(
                    symbol=symbol,
                    side=TrdSide.SELL,
                    quantity=sell_qty,
                    price=float(prices[symbol]),
                    reason=f"{plan.reason}:liquidate",
                    session=Session.NONE if market_open else Session.ETH,
                    fill_outside_rth=not market_open,
                )
            )

    return sorted(
        instructions,
        key=lambda instruction: (
            instruction.side != TrdSide.SELL,
            instruction.symbol in target_by_symbol,
            instruction.symbol,
        ),
    )


def _round_down_to_integer_signed(quantity: float) -> float:
    """Round towards zero (floor for positive, ceil for negative), then to integer.

    Preserves sign: positive values become 0, 1, 2, ... negative values become 0, -1, -2, ...
    Used for order quantities where sign direction matters.
    """
    signed_quantity = float(quantity)
    if signed_quantity > 0.0:
        normalized_quantity = floor(signed_quantity)
        return float(normalized_quantity) if normalized_quantity > 0 else 0.0
    if signed_quantity < 0.0:
        normalized_quantity = floor(abs(signed_quantity))
        return float(-normalized_quantity) if normalized_quantity > 0 else 0.0
    return 0.0