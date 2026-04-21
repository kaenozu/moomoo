from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

import pandas as pd
from moomoo import OpenSecTradeContext, OrderType, RET_OK, Session, TrdEnv, TrdMarket

from moomoo_bot.paper import PaperOrderInstruction


@dataclass
class MoomooPaperTradeClient:
    host: str = "127.0.0.1"
    port: int = 11111
    trd_env: TrdEnv = TrdEnv.SIMULATE
    trade_context: OpenSecTradeContext | None = None

    def __post_init__(self) -> None:
        if self.trade_context is None:
            self.trade_context = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=self.host, port=self.port)
        self.trade_context.start()

    def close(self) -> None:
        if self.trade_context is not None:
            self.trade_context.close()

    def get_account_value(self) -> float:
        if self.trade_context is None:
            raise RuntimeError("trade context is not initialized")
        ret, data = self.trade_context.accinfo_query(trd_env=self.trd_env)
        if ret != RET_OK:
            mode_name = "live" if self.trd_env == TrdEnv.REAL else "simulated"
            raise RuntimeError(f"Failed to fetch {mode_name} account info: {data}")
        if not isinstance(data, pd.DataFrame) or data.empty:
            mode_name = "Live" if self.trd_env == TrdEnv.REAL else "Simulated"
            raise RuntimeError(f"{mode_name} account info did not return rows")

        row = data.iloc[0]
        for field in ("total_assets", "power", "available_funds"):
            value = row.get(field)
            if value is not None and float(value) > 0.0:
                return float(value)
        raise RuntimeError("Simulated account did not expose a positive account value")

    def get_position_frame(self) -> pd.DataFrame:
        if self.trade_context is None:
            raise RuntimeError("trade context is not initialized")
        ret, data = self.trade_context.position_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            mode_name = "live" if self.trd_env == TrdEnv.REAL else "simulated"
            raise RuntimeError(f"Failed to fetch {mode_name} positions: {data}")
        if not isinstance(data, pd.DataFrame):
            mode_name = "Live" if self.trd_env == TrdEnv.REAL else "Simulated"
            raise RuntimeError(f"{mode_name} positions did not return a DataFrame")
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

    def submit_order(self, instruction: PaperOrderInstruction) -> pd.DataFrame:
        if self.trade_context is None:
            raise RuntimeError("trade context is not initialized")
        ret, data = self.trade_context.place_order(
            price=instruction.price,
            qty=instruction.quantity,
            code=instruction.symbol,
            trd_side=instruction.side,
            order_type=OrderType.NORMAL,
            trd_env=self.trd_env,
            remark=instruction.reason,
            session=instruction.session or Session.NONE,
            fill_outside_rth=instruction.fill_outside_rth,
        )
        if ret != RET_OK:
            mode_name = "live" if self.trd_env == TrdEnv.REAL else "paper"
            raise RuntimeError(f"Failed to submit {mode_name} order for {instruction.symbol}: {data}")
        if not isinstance(data, pd.DataFrame):
            mode_name = "Live" if self.trd_env == TrdEnv.REAL else "Paper"
            raise RuntimeError(f"{mode_name} order response did not return a DataFrame")
        return data
