"""Moomoo paper trade client module.

Purpose: Wrapper for Moomoo paper/live trading via OpenSecTradeContext.
Related: broker/__init__.py, paper.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from math import isclose
from time import sleep

import pandas as pd
from moomoo import (
    OpenSecTradeContext,
    OrderType,
    RET_OK,
    Session,
    TrdEnv,
    TrdMarket,
)

from moomoo_bot.exceptions import (
    BrokerConnectionError,
    DataError,
    OrderRejectedError,
    OrderTimeoutError,
)
from moomoo_bot.paper import PaperOrderInstruction
from moomoo_bot.row_utils import position_quantities_from_frame
from moomoo_bot.retry import with_retries, TRANSIENT_EXCEPTIONS

logger = logging.getLogger(__name__)

# ``with_retries`` counts retries after the initial call. Keep three total
# broker attempts, matching the previous paper-trading behavior.
_START_MAX_RETRIES = 2
_START_RETRY_DELAY_SECONDS = 2.0

_ACTIVE_ORDER_STATUS_NAMES = frozenset(
    {
        "SUBMITTED",
        "SUBMITTING",
        "WAITING_SUBMIT",
        "CANCELLING_ALL",
        "CANCELLING_PART",
        "CANCELLED_PART",
        "FILLED_PART",
    }
)


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
        last_exc: Exception | None = None
        for attempt in range(1, _START_MAX_RETRIES + 1):
            try:
                self.trade_context.start()
                return
            except Exception as exc:
                last_exc = exc
                if attempt < _START_MAX_RETRIES:
                    logger.warning(
                        "trade_context.start() failed (attempt %d/%d): %s; retrying in %.1fs",
                        attempt,
                        _START_MAX_RETRIES,
                        exc,
                        _START_RETRY_DELAY_SECONDS,
                    )
                    sleep(_START_RETRY_DELAY_SECONDS)
        raise BrokerConnectionError(
            f"Failed to start trade context after {_START_MAX_RETRIES} attempts: {last_exc}"
        ) from last_exc

    def close(self) -> None:
        if self.trade_context is not None:
            self.trade_context.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception as exc:
            logger.debug("Error closing trade context in __del__: %s", exc)

    def __enter__(self) -> MoomooPaperTradeClient:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _account_info_row(self) -> tuple[pd.Series, str]:
        if self.trade_context is None:
            raise BrokerConnectionError("trade context is not initialized")
        ret, data = self.trade_context.accinfo_query(trd_env=self.trd_env)
        if ret != RET_OK:
            mode_name = "live" if self.trd_env == TrdEnv.REAL else "simulated"
            raise BrokerConnectionError(
                f"Failed to fetch {mode_name} account info: {data}"
            )
        if not isinstance(data, pd.DataFrame) or data.empty:
            mode_name = "Live" if self.trd_env == TrdEnv.REAL else "Simulated"
            raise DataError(f"{mode_name} account info did not return rows")
        mode_name = "Live" if self.trd_env == TrdEnv.REAL else "Simulated"
        return data.iloc[0], mode_name

    def get_account_value(self) -> float:
        row, mode_name = self._account_info_row()
        for field in ("total_assets", "power", "available_funds"):
            value = _positive_float(row.get(field))
            if value is not None:
                return value
        raise DataError(f"{mode_name} account did not expose a positive account value")

    def get_buying_power(self) -> float:
        row, _mode_name = self._account_info_row()
        for field in (
            "available_funds",
            "power",
            "remaining_dtbp",
            "usd_net_cash_power",
            "avl_withdrawal_cash",
            "cash",
            "us_cash",
        ):
            value = _positive_float(row.get(field))
            if value is not None:
                return float(value)
        return 0.0

    def get_position_frame(self) -> pd.DataFrame:
        if self.trade_context is None:
            raise BrokerConnectionError("trade context is not initialized")
        ret, data = self.trade_context.position_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            mode_name = "live" if self.trd_env == TrdEnv.REAL else "simulated"
            raise BrokerConnectionError(
                f"Failed to fetch {mode_name} positions: {data}"
            )
        if not isinstance(data, pd.DataFrame):
            mode_name = "Live" if self.trd_env == TrdEnv.REAL else "Simulated"
            raise DataError(f"{mode_name} positions did not return a DataFrame")
        return data.copy()

    def get_position_quantities(self) -> dict[str, float]:
        return position_quantities_from_frame(self.get_position_frame())

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
        """Submit an order with retry logic for transient failures."""
        return self._submit_order_with_retry(instruction)

    @with_retries(
        max_retries=_START_MAX_RETRIES,
        base_delay=_START_RETRY_DELAY_SECONDS,
        exceptions=TRANSIENT_EXCEPTIONS,
        raise_on_failure=OrderTimeoutError,
    )
    def _submit_order_with_retry(
        self, instruction: PaperOrderInstruction
    ) -> pd.DataFrame:
        """Internal retry-wrapped order submission."""
        if self.trade_context is None:
            raise BrokerConnectionError("trade context is not initialized")

        remark = (instruction.reason or "")[:64]

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
        if not isinstance(data, pd.DataFrame) or data.empty:
            raise DataError(f"Broker order response invalid: {data}")
        return data


def _is_active_order_status(status: object) -> bool:
    normalized_status = str(status).strip().upper()
    return normalized_status in _ACTIVE_ORDER_STATUS_NAMES


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


def _positive_float(value: str | int | float | None) -> float | None:
    normalized_text = _normalize_text(value).upper()
    if normalized_text in {"", "N/A", "NONE", "NAN"}:
        return None
    try:
        numeric = float(normalized_text)
    except (TypeError, ValueError):
        return None
    if numeric <= 0.0:
        return None
    return numeric


def _quantity_matches(order_qty: object, instruction_qty: float) -> bool:
    broker_qty = float(_normalize_text(order_qty) or 0.0)
    requested_qty = float(instruction_qty or 0.0)
    if isclose(broker_qty, requested_qty, rel_tol=1e-6, abs_tol=0.001):
        return True

    # Some paper responses normalize quantity to whole shares in order queries.
    truncated_requested_qty = float(int(requested_qty))
    if truncated_requested_qty <= 0.0:
        return False
    return isclose(
        broker_qty,
        truncated_requested_qty,
        rel_tol=1e-6,
        abs_tol=0.001,
    )


def _session_matches(order_row: pd.Series, instruction: PaperOrderInstruction) -> bool:
    row_session = _normalize_order_session(order_row.get("session"))
    if not row_session:
        return True
    return row_session == _normalize_order_session(instruction.session)


def _fill_outside_rth_matches(
    order_row: pd.Series, instruction: PaperOrderInstruction
) -> bool:
    raw_value = order_row.get("fill_outside_rth")
    normalized_text = _normalize_text(raw_value)
    if not normalized_text:
        return True
    return _normalize_order_bool(raw_value) == bool(instruction.fill_outside_rth)


def _order_matches_instruction(
    order_row: pd.Series, instruction: PaperOrderInstruction
) -> bool:
    return (
        _normalize_text(order_row.get("code")) == instruction.symbol
        and _normalize_text(order_row.get("trd_side")).upper()
        == str(instruction.side).upper()
        and _quantity_matches(order_row.get("qty", 0.0), float(instruction.quantity))
        and isclose(
            float(order_row.get("price", 0.0) or 0.0),
            float(instruction.price),
            rel_tol=1e-6,
            abs_tol=0.001,
        )
        and _session_matches(order_row, instruction)
        and _fill_outside_rth_matches(order_row, instruction)
        and _normalize_text(order_row.get("remark"))
        == _normalize_text(instruction.reason)
    )
