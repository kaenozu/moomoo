"""Moomoo paper trade client module.

Purpose: Wrapper for Moomoo paper/live trading via OpenSecTradeContext.
Related: broker/__init__.py, paper.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose

import pandas as pd
from moomoo import (
    OpenSecTradeContext,
    OrderStatus,
    OrderType,
    RET_OK,
    Session,
    TrdEnv,
    TrdMarket,
)

from moomoo_bot.exceptions import BrokerConnectionError, DataError, OrderRejectedError
from moomoo_bot.paper import PaperOrderInstruction


_ACTIVE_ORDER_STATUSES = {
    OrderStatus.SUBMITTED,
    OrderStatus.SUBMITTING,
    OrderStatus.WAITING_SUBMIT,
    OrderStatus.CANCELLING_ALL,
    OrderStatus.CANCELLING_PART,
    OrderStatus.CANCELLED_PART,
    OrderStatus.FILLED_PART,
}


@dataclass
class MoomooPaperTradeClient:
    host: str = "127.0.0.1"
    port: int = 11111
    trd_env: TrdEnv = TrdEnv.SIMULATE
    trade_context: OpenSecTradeContext | None = None

    def __post_init__(self) -> None:
        if self.trade_context is None:
            self.trade_context = OpenSecTradeContext(
                filter_trdmarket=TrdMarket.US, host=self.host, port=self.port
            )
        self.trade_context.start()

    def close(self) -> None:
        if self.trade_context is not None:
            self.trade_context.close()

    def get_account_value(self) -> float:
        if self.trade_context is None:
            raise BrokerConnectionError("trade context is not initialized")
        ret, data = self.trade_context.accinfo_query(trd_env=self.trd_env)
        if ret != RET_OK:
            mode_name = "live" if self.trd_env == TrdEnv.REAL else "simulated"
            raise BrokerConnectionError(f"Failed to fetch {mode_name} account info: {data}")
        if not isinstance(data, pd.DataFrame) or data.empty:
            mode_name = "Live" if self.trd_env == TrdEnv.REAL else "Simulated"
            raise DataError(f"{mode_name} account info did not return rows")

        row = data.iloc[0]
        for field in ("total_assets", "power", "available_funds"):
            value = row.get(field)
            if value is not None and float(value) > 0.0:
                return float(value)
        raise DataError("Simulated account did not expose a positive account value")

    def get_position_frame(self) -> pd.DataFrame:
        if self.trade_context is None:
            raise BrokerConnectionError("trade context is not initialized")
        ret, data = self.trade_context.position_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            mode_name = "live" if self.trd_env == TrdEnv.REAL else "simulated"
            raise BrokerConnectionError(f"Failed to fetch {mode_name} positions: {data}")
        if not isinstance(data, pd.DataFrame):
            mode_name = "Live" if self.trd_env == TrdEnv.REAL else "Simulated"
            raise DataError(f"{mode_name} positions did not return a DataFrame")
        return data.copy()

    def get_position_quantities(self) -> dict[str, float]:
        data = self.get_position_frame()
        positions: dict[str, float] = {}
        for _, row in data.iterrows():
            code = str(row.get("code", "")).strip()
            if not code:
                continue
            qty = float(row.get("qty", 0.0) or 0.0)
            if qty > 0.0:
                positions[code] = qty
        return positions

    def get_order_frame(self, refresh_cache: bool = True) -> pd.DataFrame:
        if self.trade_context is None:
            raise BrokerConnectionError("trade context is not initialized")
        ret, data = self.trade_context.order_list_query(
            trd_env=self.trd_env, refresh_cache=refresh_cache
        )
        if ret != RET_OK:
            mode_name = "live" if self.trd_env == TrdEnv.REAL else "paper"
            raise BrokerConnectionError(f"Failed to fetch {mode_name} orders: {data}")
        if not isinstance(data, pd.DataFrame):
            mode_name = "Live" if self.trd_env == TrdEnv.REAL else "Paper"
            raise DataError(f"{mode_name} orders did not return a DataFrame")
        return data.copy()

    def get_active_order_frame(self, refresh_cache: bool = True) -> pd.DataFrame:
        order_frame = self.get_order_frame(refresh_cache=refresh_cache)
        if order_frame.empty:
            return order_frame

        active_rows = [
            row
            for _, row in order_frame.iterrows()
            if _is_active_order_status(row.get("order_status"))
        ]
        if not active_rows:
            return order_frame.iloc[0:0].copy()
        return pd.DataFrame(active_rows, columns=order_frame.columns)

    def get_matching_active_order(
        self, instruction: PaperOrderInstruction, refresh_cache: bool = True
    ) -> dict[str, object] | None:
        active_orders = self.get_active_order_frame(refresh_cache=refresh_cache)
        for _, row in active_orders.iterrows():
            if _order_matches_instruction(row, instruction):
                return row.to_dict()
        return None

    def submit_order(self, instruction: PaperOrderInstruction) -> pd.DataFrame:
        if self.trade_context is None:
            raise BrokerConnectionError("trade context is not initialized")
        # Limit remark to 64 bytes (UTF-8)
        remark = instruction.reason or ""
        remark_bytes = remark.encode("utf-8")[:64]
        remark = remark_bytes.decode("utf-8", errors="ignore")
        ret, data = self.trade_context.place_order(
            price=instruction.price,
            qty=instruction.quantity,
            code=instruction.symbol,
            trd_side=instruction.side,
            order_type=OrderType.NORMAL,
            trd_env=self.trd_env,
            remark=remark,
            session=instruction.session or Session.NONE,
            fill_outside_rth=instruction.fill_outside_rth,
        )
        if ret != RET_OK:
            mode_name = "live" if self.trd_env == TrdEnv.REAL else "paper"
            raise OrderRejectedError(
                f"Failed to submit {mode_name} order for {instruction.symbol}: {data}"
            )
        if not isinstance(data, pd.DataFrame):
            mode_name = "Live" if self.trd_env == TrdEnv.REAL else "Paper"
            raise DataError(f"{mode_name} order response did not return a DataFrame")
        return data


def _is_active_order_status(status: object) -> bool:
    normalized_status = str(status).strip().upper()
    return normalized_status in _ACTIVE_ORDER_STATUSES


def _normalize_text(value: object) -> str:
    normalized = str(value).strip()
    return "" if normalized.upper() in {"", "N/A", "NONE", "NAN"} else normalized


def _normalize_order_session(value: object) -> str:
    return _normalize_text(value).upper()


def _normalize_order_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _normalize_text(value).upper()
    return normalized in {"TRUE", "1", "YES", "Y"}


def _order_matches_instruction(
    order_row: pd.Series, instruction: PaperOrderInstruction
) -> bool:
    return (
        _normalize_text(order_row.get("code")) == instruction.symbol
        and _normalize_text(order_row.get("trd_side")).upper()
        == str(instruction.side).upper()
        and isclose(
            float(order_row.get("qty", 0.0) or 0.0),
            float(instruction.quantity),
            rel_tol=1e-6,
            abs_tol=0.001,
        )
        and isclose(
            float(order_row.get("price", 0.0) or 0.0),
            float(instruction.price),
            rel_tol=1e-6,
            abs_tol=0.001,
        )
        and _normalize_order_session(order_row.get("session"))
        == _normalize_order_session(instruction.session)
        and _normalize_order_bool(order_row.get("fill_outside_rth"))
        == bool(instruction.fill_outside_rth)
        and _normalize_text(order_row.get("remark"))
        == _normalize_text(instruction.reason)
    )
