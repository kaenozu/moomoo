"""State persistence type definitions.

Purpose: Dataclass definitions and Row→Dataclass converters for trading state persistence.
         Extracted from state.py to keep file sizes manageable.
Related: state.py, state_schema.py, orchestrator/, risk.py.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class PersistentRiskState:
    peak_account_value: float | None = None
    halted: bool = False
    halted_reason: str | None = None
    drawdown_tier: int = 0  # 0=normal, 1=reduced(50%), 2=liquidated
    daily_order_count: int = 0
    daily_order_date: str | None = None
    last_equity_value: float | None = None
    updated_at: str | None = None
    rule_violation_count: int = 0


@dataclass
class OrderRecord:
    order_id: str | None = None
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    status: str = "submitted"
    reason: str = ""
    filled_quantity: float = 0.0
    submitted_at: str | None = None
    filled_at: str | None = None
    broker_accepted_price: float | None = None
    avg_fill_price: float | None = None
    cumulative_fee_amount: float = 0.0
    cumulative_slippage_amount: float = 0.0


@dataclass
class ExecutionFillRecord:
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    fill_quantity: float = 0.0
    intended_price: float = 0.0
    broker_accepted_price: float | None = None
    fill_price: float = 0.0
    fee_amount: float = 0.0
    slippage_amount: float = 0.0
    filled_at: str | None = None


@dataclass
class TaxLotRecord:
    lot_id: int | None = None
    symbol: str = ""
    opening_order_id: str | None = None
    opened_at: str | None = None
    original_quantity: float = 0.0
    remaining_quantity: float = 0.0
    cost_basis_price: float = 0.0
    status: str = "open"
    closing_order_id: str | None = None
    closed_at: str | None = None


@dataclass
class TaxLotRealizationRecord:
    realization_id: int | None = None
    lot_id: int | None = None
    symbol: str = ""
    sell_order_id: str | None = None
    quantity: float = 0.0
    opening_price: float | None = None
    closing_price: float = 0.0
    fee_amount: float = 0.0
    slippage_amount: float = 0.0
    realized_pnl: float | None = None
    opened_at: str | None = None
    closed_at: str | None = None


@dataclass
class EquitySnapshot:
    timestamp: str = ""
    account_value: float = 0.0
    cash: float = 0.0
    positions_json: str = "{}"
    market_date: str | None = None


@dataclass
class ExecutionAuditSummary:
    order_count: int = 0
    pending_order_count: int = 0
    fill_count: int = 0
    buy_fill_count: int = 0
    sell_fill_count: int = 0
    realization_count: int = 0
    open_lot_count: int = 0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    realized_pnl: float = 0.0
    last_fill_at: str | None = None
    last_realization_at: str | None = None


def _order_record_from_row(row: sqlite3.Row) -> OrderRecord:
    return OrderRecord(
        order_id=row["order_id"],
        symbol=row["symbol"],
        side=row["side"],
        quantity=row["quantity"],
        price=row["price"],
        status=row["status"],
        reason=row["reason"],
        filled_quantity=row["filled_quantity"],
        submitted_at=row["submitted_at"],
        filled_at=row["filled_at"],
        broker_accepted_price=row["broker_accepted_price"],
        avg_fill_price=row["avg_fill_price"],
        cumulative_fee_amount=row["cumulative_fee_amount"] or 0.0,
        cumulative_slippage_amount=row["cumulative_slippage_amount"] or 0.0,
    )


def _execution_fill_record_from_row(row: sqlite3.Row) -> ExecutionFillRecord:
    return ExecutionFillRecord(
        order_id=row["order_id"],
        symbol=row["symbol"],
        side=row["side"],
        fill_quantity=row["fill_quantity"],
        intended_price=row["intended_price"],
        broker_accepted_price=row["broker_accepted_price"],
        fill_price=row["fill_price"],
        fee_amount=row["fee_amount"],
        slippage_amount=row["slippage_amount"],
        filled_at=row["filled_at"],
    )


def _tax_lot_record_from_row(row: sqlite3.Row) -> TaxLotRecord:
    return TaxLotRecord(
        lot_id=row["id"],
        symbol=row["symbol"],
        opening_order_id=row["opening_order_id"],
        opened_at=row["opened_at"],
        original_quantity=row["original_quantity"],
        remaining_quantity=row["remaining_quantity"],
        cost_basis_price=row["cost_basis_price"],
        status=row["status"],
        closing_order_id=row["closing_order_id"],
        closed_at=row["closed_at"],
    )


def _tax_lot_realization_from_row(row: sqlite3.Row) -> TaxLotRealizationRecord:
    return TaxLotRealizationRecord(
        realization_id=row["id"],
        lot_id=row["lot_id"],
        symbol=row["symbol"],
        sell_order_id=row["sell_order_id"],
        quantity=row["quantity"],
        opening_price=row["opening_price"],
        closing_price=row["closing_price"],
        fee_amount=row["fee_amount"],
        slippage_amount=row["slippage_amount"],
        realized_pnl=row["realized_pnl"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
    )


def _equity_snapshot_from_row(row: sqlite3.Row) -> EquitySnapshot:
    return EquitySnapshot(
        timestamp=row["timestamp"],
        account_value=row["account_value"],
        cash=row["cash"],
        positions_json=row["positions_json"],
        market_date=row["market_date"],
    )
