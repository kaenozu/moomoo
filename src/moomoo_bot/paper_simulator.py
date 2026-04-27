"""Local paper trading simulator.

Purpose: Broker-independent paper account simulator with local persistence.
Related: ui/paper_studio.py.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path

from moomoo_bot.row_utils import utc_now_iso


_DEFAULT_SIM_DB = Path.home() / ".moomoo_bot" / "paper-sim-state.json"


@dataclass
class SimPosition:
    symbol: str
    quantity: float
    avg_cost: float


@dataclass
class SimTrade:
    trade_id: int
    timestamp: str
    symbol: str
    side: str
    quantity: float
    price: float
    notional: float
    realized_pnl: float


@dataclass
class SimSnapshot:
    timestamp: str
    cash: float
    market_value: float
    equity: float
    unrealized_pnl: float
    realized_pnl: float


class PaperSimulator:
    def __init__(
        self,
        state_path: Path | None = None,
        initial_cash: float = 100_000.0,
    ) -> None:
        self.state_path = state_path or _DEFAULT_SIM_DB
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        self.cash: float = float(initial_cash)
        self.realized_pnl: float = 0.0
        self.positions: dict[str, SimPosition] = {}
        self.trades: list[SimTrade] = []
        self.equity_curve: list[SimSnapshot] = []

    @classmethod
    def load(
        cls,
        state_path: Path | None = None,
        initial_cash: float = 100_000.0,
    ) -> "PaperSimulator":
        simulator = cls(state_path=state_path, initial_cash=initial_cash)
        if not simulator.state_path.exists():
            return simulator

        data = json.loads(simulator.state_path.read_text(encoding="utf-8"))
        simulator.cash = float(data.get("cash", initial_cash))
        simulator.realized_pnl = float(data.get("realized_pnl", 0.0))

        simulator.positions = {
            symbol: SimPosition(
                symbol=symbol,
                quantity=float(payload.get("quantity", 0.0)),
                avg_cost=float(payload.get("avg_cost", 0.0)),
            )
            for symbol, payload in data.get("positions", {}).items()
        }
        simulator.trades = [
            SimTrade(
                trade_id=int(payload["trade_id"]),
                timestamp=str(payload["timestamp"]),
                symbol=str(payload["symbol"]),
                side=str(payload["side"]),
                quantity=float(payload["quantity"]),
                price=float(payload["price"]),
                notional=float(payload["notional"]),
                realized_pnl=float(payload.get("realized_pnl", 0.0)),
            )
            for payload in data.get("trades", [])
        ]
        simulator.equity_curve = [
            SimSnapshot(
                timestamp=str(payload["timestamp"]),
                cash=float(payload["cash"]),
                market_value=float(payload["market_value"]),
                equity=float(payload["equity"]),
                unrealized_pnl=float(payload["unrealized_pnl"]),
                realized_pnl=float(payload.get("realized_pnl", 0.0)),
            )
            for payload in data.get("equity_curve", [])
        ]
        return simulator

    def save(self) -> None:
        payload = {
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "positions": {
                symbol: {
                    "quantity": position.quantity,
                    "avg_cost": position.avg_cost,
                }
                for symbol, position in self.positions.items()
                if position.quantity > 0.0
            },
            "trades": [asdict(trade) for trade in self.trades],
            "equity_curve": [asdict(snapshot) for snapshot in self.equity_curve],
        }
        tmp_path = self.state_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.state_path)

    def reset(self, initial_cash: float = 100_000.0) -> None:
        self.cash = float(initial_cash)
        self.realized_pnl = 0.0
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        self.save()

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
    ) -> SimTrade:
        normalized_symbol = str(symbol).strip().upper()
        normalized_side = str(side).strip().upper()
        qty = float(quantity)
        px = float(price)

        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if not normalized_symbol:
            raise ValueError("symbol must not be empty")
        if qty <= 0.0:
            raise ValueError("quantity must be positive")
        if px <= 0.0:
            raise ValueError("price must be positive")

        notional = qty * px
        realized = 0.0

        current = self.positions.get(normalized_symbol)
        if current is None:
            current = SimPosition(symbol=normalized_symbol, quantity=0.0, avg_cost=0.0)

        if normalized_side == "BUY":
            if notional > self.cash:
                raise ValueError("insufficient cash")
            new_qty = current.quantity + qty
            new_avg_cost = (
                (current.avg_cost * current.quantity) + notional
            ) / new_qty
            current.quantity = new_qty
            current.avg_cost = new_avg_cost
            self.cash -= notional
        else:
            if qty > current.quantity:
                raise ValueError("insufficient position")
            realized = (px - current.avg_cost) * qty
            current.quantity -= qty
            self.cash += notional
            self.realized_pnl += realized

        if current.quantity <= 0.0:
            self.positions.pop(normalized_symbol, None)
        else:
            self.positions[normalized_symbol] = current

        trade = SimTrade(
            trade_id=len(self.trades) + 1,
            timestamp=utc_now_iso(),
            symbol=normalized_symbol,
            side=normalized_side,
            quantity=qty,
            price=px,
            notional=notional,
            realized_pnl=realized,
        )
        self.trades.append(trade)
        return trade

    def mark_to_market(self, prices: dict[str, float]) -> SimSnapshot:
        market_value = 0.0
        unrealized_pnl = 0.0
        for symbol, position in self.positions.items():
            price = float(prices.get(symbol, position.avg_cost))
            market_value += position.quantity * price
            unrealized_pnl += (price - position.avg_cost) * position.quantity

        equity = self.cash + market_value
        snapshot = SimSnapshot(
            timestamp=utc_now_iso(),
            cash=self.cash,
            market_value=market_value,
            equity=equity,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=self.realized_pnl,
        )
        self.equity_curve.append(snapshot)
        return snapshot
