"""Streamlit UI for local broker-independent paper trading."""

from __future__ import annotations

import importlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from moomoo import TrdEnv

from moomoo_bot import orchestrator
from moomoo_bot.config import get_settings
from moomoo_bot.paper_simulator import PaperSimulator
from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.orchestrator.helpers import snapshot_latest_prices


DEFAULT_SYMBOLS = ["US.AAPL", "US.MSFT", "US.NVDA", "US.AMZN", "US.VT"]
AUTO_REFRESH_SECONDS = 5
PRICE_HISTORY_LIMIT = 120
QUERY_KEY_VIEW = "view"
QUERY_KEY_SYMBOL = "symbol"
VIEW_SYMBOL_DETAIL = "symbol_detail"
RUN_PAPER_TRADE_CAPITAL = 100_000.0
RUN_PAPER_TRADE_HISTORY_DAYS = 2200
RUN_PAPER_TRADE_MINIMUM_ORDER_VALUE = 5.0


def _apply_styles() -> None:
    st.markdown(
        """
        <style>
          :root {
                                                --bg: #0b1220;
                                                --bg-alt: #111827;
                                                --panel: #111827;
                                                --panel-strong: #1f2937;
                                                --panel-soft: #0f172a;
                                                --text: #e5e7eb;
                                                --text-muted: #94a3b8;
                                                --text-inverse: #f8fafc;
                                                --border: #334155;
                                                --border-strong: #475569;
                                                --accent: #f97316;
                                                --accent-2: #38bdf8;
                                                --accent-3: #14b8a6;
                                                color-scheme: dark;
          }

                    html,
                    body,
                    .stApp,
                    [data-testid="stAppViewContainer"] {
                        background:
                            radial-gradient(circle at top right, rgba(56, 189, 248, 0.12), transparent 26%),
                            radial-gradient(circle at left top, rgba(249, 115, 22, 0.10), transparent 20%),
                            linear-gradient(180deg, #020617 0%, var(--bg) 100%);
                        color: var(--text);
          }

                    .stApp {
                        color: var(--text);
          }

                    [data-testid="stHeader"],
                    [data-testid="stToolbar"] {
                        background: transparent;
          }

                    .block-container {
                        padding-top: 1rem;
                        padding-bottom: 2.5rem;
                        max-width: 1320px;
          }

                    h1,
                    h2,
                    h3,
                    h4 {
                        color: var(--text);
                        font-weight: 700;
                        letter-spacing: 0.01em;
          }

                    p,
                    li,
                    label,
                    small,
                    .stCaption,
                    [data-testid="stMarkdownContainer"] {
                        color: var(--text-muted) !important;
                    }

                    .stMarkdown a,
                    a {
                        color: var(--accent-2);
                    }

          [data-testid="stSidebar"] {
                        background: linear-gradient(180deg, #0f172a 0%, #111827 100%);
                        border-right: 1px solid rgba(255, 255, 255, 0.08);
                    }

                    [data-testid="stSidebar"] *,
                    [data-testid="stSidebar"] label,
                    [data-testid="stSidebar"] p,
                    [data-testid="stSidebar"] span,
                    [data-testid="stSidebar"] small,
                    [data-testid="stSidebar"] li,
                    [data-testid="stSidebar"] h1,
                    [data-testid="stSidebar"] h2,
                    [data-testid="stSidebar"] h3,
                    [data-testid="stSidebar"] h4 {
                        color: #e5eefb !important;
                    }

                    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {
                        color: #cbd5e1 !important;
                    }

                    [data-testid="stSidebar"] [data-baseweb="input"],
                    [data-testid="stSidebar"] [data-baseweb="select"],
                    [data-testid="stSidebar"] textarea,
                    [data-testid="stSidebar"] input {
                        background: rgba(15, 23, 42, 0.72) !important;
                        color: #f8fafc !important;
                    }

                    [data-testid="stSidebar"] [data-baseweb="input"] *,
                    [data-testid="stSidebar"] [data-baseweb="select"] *,
                    [data-testid="stSidebar"] textarea *,
                    [data-testid="stSidebar"] input * {
                        color: #f8fafc !important;
                    }

                    [data-testid="stSidebar"] [data-testid="stButton"] button {
                        border-color: rgba(148, 163, 184, 0.45) !important;
          }

          .app-note {
                        background: linear-gradient(135deg, rgba(249, 115, 22, 0.18) 0%, rgba(15, 23, 42, 0.96) 100%);
                        color: #f8fafc;
                        border: 1px solid rgba(249, 115, 22, 0.32);
                        border-left: 5px solid var(--accent);
                        padding: 0.85rem 1rem;
                        border-radius: 14px;
                        margin-bottom: 0.85rem;
                        box-shadow: 0 10px 25px rgba(2, 6, 23, 0.30);
                    }

                    [data-testid="stMetric"] {
                        background: var(--panel);
                        border: 1px solid var(--border);
                        border-radius: 14px;
                        padding: 0.8rem 0.9rem;
                        box-shadow: 0 10px 24px rgba(15, 23, 42, 0.04);
                    }

                    [data-testid="stMetric"] * {
                        color: var(--text) !important;
                    }

                    [data-testid="stMetric"] label,
                    [data-testid="stMetric"] div {
                        color: var(--text) !important;
                    }

                    [data-testid="stForm"] {
                        background: var(--panel);
                        border: 1px solid var(--border);
                        border-radius: 18px;
                        padding: 1rem;
                        box-shadow: 0 12px 28px rgba(15, 23, 42, 0.04);
                    }

                    [data-testid="stDataFrame"],
                    [data-testid="stTable"],
                    [data-testid="stChart"] {
                        background: var(--panel);
                        border: 1px solid var(--border);
                        border-radius: 14px;
                        overflow: hidden;
                    }

                    [data-baseweb="tab-list"] {
                        gap: 0.5rem;
                        background: transparent;
                        border-bottom: 1px solid var(--border);
                    }

                    [data-baseweb="tab"] {
                        background: var(--panel);
                        color: var(--text-muted);
                        border: 1px solid var(--border);
                        border-bottom: none;
                        border-radius: 999px 999px 0 0;
                        padding: 0.45rem 0.85rem;
                    }

                    [aria-selected="true"][data-baseweb="tab"] {
                        background: var(--panel-strong);
                        color: var(--text-inverse);
                        border-color: var(--panel-strong);
                    }

                    .stButton > button,
                    [data-testid="baseButton-secondary"] {
                        background: var(--panel);
                        color: var(--text);
                        border: 1px solid var(--border);
                        border-radius: 12px;
                        box-shadow: none;
                    }

                    .stButton > button:hover,
                    [data-testid="baseButton-secondary"]:hover {
                        border-color: var(--accent-2);
                        color: var(--accent-2);
                    }

                    .stButton > button[kind="primary"],
                    [data-testid="baseButton-primary"] {
                        background: linear-gradient(135deg, var(--accent) 0%, #f97316 100%);
                        color: #ffffff;
                        border: none;
                        box-shadow: 0 10px 24px rgba(234, 88, 12, 0.20);
                    }

                    .stButton > button[kind="primary"]:hover,
                    [data-testid="baseButton-primary"]:hover {
                        filter: brightness(1.03);
                    }

                    [data-baseweb="input"] input,
                    [data-baseweb="textarea"] textarea,
                    [data-baseweb="select"] > div {
                        background: var(--panel);
                        color: var(--text);
                        border-color: var(--border);
                    }

                    [data-testid="stAlert"] {
                        border-radius: 14px;
                        border-width: 1px;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _reload_orchestrator_runtime():
    from moomoo_bot.orchestrator import cycle as orchestrator_cycle

    importlib.reload(orchestrator_cycle)
    return importlib.reload(orchestrator)


def _simulator_state_mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _normalize_query_symbol(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    symbol = str(value or "").strip().upper()
    return symbol or None


def _current_detail_symbol() -> str | None:
    if st.query_params.get(QUERY_KEY_VIEW) != VIEW_SYMBOL_DETAIL:
        return None
    return _normalize_query_symbol(st.query_params.get(QUERY_KEY_SYMBOL))


def _open_symbol_detail(symbol: str) -> None:
    st.query_params[QUERY_KEY_VIEW] = VIEW_SYMBOL_DETAIL
    st.query_params[QUERY_KEY_SYMBOL] = symbol
    st.rerun()


def _close_symbol_detail() -> None:
    if QUERY_KEY_VIEW in st.query_params:
        del st.query_params[QUERY_KEY_VIEW]
    if QUERY_KEY_SYMBOL in st.query_params:
        del st.query_params[QUERY_KEY_SYMBOL]
    st.rerun()


def _default_prices(symbols: list[str]) -> dict[str, float]:
    return {symbol: 100.0 + idx * 20.0 for idx, symbol in enumerate(symbols)}


def _fetch_live_prices(
    symbols: list[str],
    host: str,
    port: int,
    fallback_prices: dict[str, float],
) -> tuple[dict[str, float], str]:
    quote_client = MoomooOpenDClient(host=host, port=port)
    try:
        latest = snapshot_latest_prices(quote_client, symbols)
    finally:
        quote_client.close()

    if not latest:
        raise RuntimeError("OpenD スナップショットから有効な価格を取得できませんでした")

    merged = dict(fallback_prices)
    for symbol in symbols:
        if symbol in latest and float(latest[symbol]) > 0.0:
            merged[symbol] = float(latest[symbol])
    return merged, datetime.now(timezone.utc).isoformat()


def _portfolio_symbols(simulator: PaperSimulator) -> list[str]:
    return sorted(simulator.positions.keys())


def _price_history_key(symbol: str) -> str:
    return f"price_history::{symbol}"


def _price_history_seed_key(symbol: str) -> str:
    return f"price_history_seeded::{symbol}"


def _append_price_history(symbol: str, timestamp: str, price: float) -> None:
    key = _price_history_key(symbol)
    history = st.session_state.get(key)
    if not isinstance(history, list):
        history = []
    history.append({"timestamp": timestamp, "price": float(price)})
    if len(history) > PRICE_HISTORY_LIMIT:
        history = history[-PRICE_HISTORY_LIMIT:]
    st.session_state[key] = history


def _price_history_frame(symbol: str) -> pd.DataFrame:
    history = st.session_state.get(_price_history_key(symbol))
    if not isinstance(history, list) or not history:
        return pd.DataFrame(columns=["timestamp", "price"])
    frame = pd.DataFrame(history)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], format="mixed", utc=True)
    return frame.sort_values("timestamp")


def _seed_price_history_if_needed(
    symbol: str,
    opend_host: str,
    opend_port: int,
    *,
    history_days: int = 180,
) -> None:
    if st.session_state.get(_price_history_seed_key(symbol)):
        return

    quote_client = MoomooOpenDClient(host=opend_host, port=opend_port)
    try:
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=history_days)
        history = quote_client.fetch_history(
            symbol,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
        )
    except Exception:
        return
    finally:
        quote_client.close()

    if history.empty or "close" not in history.columns:
        return

    seeded_history = [
        {
            "timestamp": pd.Timestamp(timestamp).isoformat(),
            "price": float(row["close"]),
        }
        for timestamp, row in history.iterrows()
        if pd.notna(row.get("close"))
    ]
    if not seeded_history:
        return

    existing_history = st.session_state.get(_price_history_key(symbol))
    if isinstance(existing_history, list) and existing_history:
        seeded_history.extend(existing_history)

    merged_history = {
        str(entry["timestamp"]): {
            "timestamp": str(entry["timestamp"]),
            "price": float(entry["price"]),
        }
        for entry in seeded_history
        if entry.get("timestamp") and entry.get("price") is not None
    }
    ordered_history = sorted(
        merged_history.values(), key=lambda entry: entry["timestamp"]
    )
    st.session_state[_price_history_key(symbol)] = ordered_history[
        -PRICE_HISTORY_LIMIT:
    ]
    st.session_state[_price_history_seed_key(symbol)] = True


def _append_live_equity(timestamp: str, equity: float, unrealized_pnl: float) -> None:
    history = st.session_state.get("live_equity_history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "timestamp": timestamp,
            "equity": float(equity),
            "unrealized_pnl": float(unrealized_pnl),
        }
    )
    if len(history) > PRICE_HISTORY_LIMIT:
        history = history[-PRICE_HISTORY_LIMIT:]
    st.session_state["live_equity_history"] = history


def _live_equity_frame() -> pd.DataFrame:
    history = st.session_state.get("live_equity_history")
    if not isinstance(history, list) or not history:
        return pd.DataFrame(columns=["timestamp", "equity", "unrealized_pnl"])
    frame = pd.DataFrame(history)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], format="mixed", utc=True)
    return frame.sort_values("timestamp")


def _portfolio_snapshot(
    simulator: PaperSimulator, prices: dict[str, float]
) -> dict[str, float]:
    market_value = 0.0
    unrealized_pnl = 0.0
    for symbol, position in simulator.positions.items():
        last_price = float(prices.get(symbol, position.avg_cost))
        market_value += position.quantity * last_price
        unrealized_pnl += (last_price - position.avg_cost) * position.quantity

    equity = simulator.cash + market_value
    return {
        "equity": equity,
        "cash": simulator.cash,
        "market_value": market_value,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": simulator.realized_pnl,
    }


def _format_trade_summary_line(trade) -> str:
    side = str(getattr(trade, "side", "")).upper()
    action = "購入" if side == "BUY" else "売却"
    quantity = float(getattr(trade, "quantity", 0.0))
    if abs(quantity - round(quantity)) < 1e-9:
        quantity_text = f"{int(round(quantity))}"
    else:
        quantity_text = f"{quantity:.4f}"
    symbol = str(getattr(trade, "symbol", "")).strip() or "UNKNOWN"
    return f"{symbol} を {quantity_text}株{action}"


def _build_execution_report(
    before_trade_count: int,
    after_simulator: PaperSimulator,
) -> dict[str, object]:
    new_trades = after_simulator.trades[max(0, before_trade_count) :]
    summary_lines = [_format_trade_summary_line(trade) for trade in new_trades]
    rows = [
        {
            "銘柄": trade.symbol,
            "売買": "購入" if str(trade.side).upper() == "BUY" else "売却",
            "数量": round(trade.quantity, 4),
            "価格": round(trade.price, 2),
            "約定金額": round(trade.notional, 2),
        }
        for trade in new_trades
    ]
    latest_snapshot = (
        after_simulator.equity_curve[-1] if after_simulator.equity_curve else None
    )
    return {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "trade_count": len(new_trades),
        "summary_lines": summary_lines,
        "rows": rows,
        "cash": after_simulator.cash,
        "equity": latest_snapshot.equity
        if latest_snapshot is not None
        else after_simulator.cash,
        "market_value": latest_snapshot.market_value
        if latest_snapshot is not None
        else 0.0,
        "unrealized_pnl": (
            latest_snapshot.unrealized_pnl if latest_snapshot is not None else 0.0
        ),
    }


def _run_strategy_from_ui(
    *,
    settings,
    state_path_text: str,
    initial_cash: float,
) -> dict[str, object]:
    state_path = Path(state_path_text)
    state_db_path = state_path.with_suffix(".db")
    before_simulator = PaperSimulator.load(
        state_path=state_path, initial_cash=initial_cash
    )
    before_trade_count = len(before_simulator.trades)

    # Streamlit keeps imported modules cached across reruns, so refresh the
    # orchestrator cycle module first and then the package facade.
    orchestrator_runtime = _reload_orchestrator_runtime()

    orchestrator_runtime.run_one_shot_trade(
        settings=settings,
        trade_env=TrdEnv.SIMULATE,
        symbols=settings.symbol_list,
        benchmark_symbol=settings.benchmark_symbol,
        history_days=RUN_PAPER_TRADE_HISTORY_DAYS,
        capital=RUN_PAPER_TRADE_CAPITAL,
        fx_jpy_per_usd=None,
        minimum_order_value=RUN_PAPER_TRADE_MINIMUM_ORDER_VALUE,
        max_position_weight=settings.live_max_position_weight,
        use_local_sim=True,
        local_sim_path=state_path,
        local_sim_state_db_path=state_db_path,
    )

    after_simulator = PaperSimulator.load(
        state_path=state_path, initial_cash=initial_cash
    )
    return _build_execution_report(before_trade_count, after_simulator)


def _load_simulator(path: Path, initial_cash: float) -> PaperSimulator:
    key_path = st.session_state.get("sim_path")
    stored_mtime_ns = st.session_state.get("simulator_state_mtime_ns")
    current_mtime_ns = _simulator_state_mtime_ns(path)
    if (
        key_path != str(path)
        or stored_mtime_ns != current_mtime_ns
        or "simulator" not in st.session_state
    ):
        st.session_state["sim_path"] = str(path)
        st.session_state["simulator_state_mtime_ns"] = current_mtime_ns
        st.session_state["simulator"] = PaperSimulator.load(
            state_path=path,
            initial_cash=initial_cash,
        )
    return st.session_state["simulator"]


def _positions_df(simulator: PaperSimulator, prices: dict[str, float]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for symbol, position in sorted(simulator.positions.items()):
        last_price = float(prices.get(symbol, position.avg_cost))
        market_value = position.quantity * last_price
        unrealized = (last_price - position.avg_cost) * position.quantity
        rows.append(
            {
                "銘柄": symbol,
                "数量": round(position.quantity, 4),
                "平均取得価格": round(position.avg_cost, 2),
                "現在価格": round(last_price, 2),
                "時価": round(market_value, 2),
                "含み損益": round(unrealized, 2),
            }
        )
    return pd.DataFrame(rows)


def _trades_df(simulator: PaperSimulator) -> pd.DataFrame:
    rows = [
        {
            "ID": trade.trade_id,
            "時刻": trade.timestamp,
            "銘柄": trade.symbol,
            "売買": trade.side,
            "数量": round(trade.quantity, 4),
            "価格": round(trade.price, 2),
            "約定金額": round(trade.notional, 2),
            "実現損益": round(trade.realized_pnl, 2),
        }
        for trade in reversed(simulator.trades)
    ]
    return pd.DataFrame(rows)


def _equity_df(simulator: PaperSimulator) -> pd.DataFrame:
    rows = [
        {
            "timestamp": snapshot.timestamp,
            "equity": snapshot.equity,
            "unrealized_pnl": snapshot.unrealized_pnl,
            "cash": snapshot.cash,
            "market_value": snapshot.market_value,
        }
        for snapshot in simulator.equity_curve
    ]
    return pd.DataFrame(rows)


def _equity_chart_frames(
    live_equity_df: pd.DataFrame,
    stored_equity_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    combined = pd.concat(
        [
            live_equity_df[["timestamp", "equity", "unrealized_pnl"]]
            if not live_equity_df.empty
            else pd.DataFrame(columns=["timestamp", "equity", "unrealized_pnl"]),
            stored_equity_df[["timestamp", "equity", "unrealized_pnl"]]
            if not stored_equity_df.empty
            else pd.DataFrame(columns=["timestamp", "equity", "unrealized_pnl"]),
        ],
        ignore_index=True,
    )
    if combined.empty:
        return (
            pd.DataFrame(columns=["equity"]),
            pd.DataFrame(columns=["unrealized_pnl"]),
        )

    combined["timestamp"] = pd.to_datetime(
        combined["timestamp"], format="mixed", utc=True
    )
    combined = (
        combined.drop_duplicates(
            subset=["timestamp", "equity", "unrealized_pnl"],
            keep="last",
        )
        .sort_values("timestamp")
        .set_index("timestamp")
    )
    return combined[["equity"]], combined[["unrealized_pnl"]]


def _render_execution_report(report: dict[str, object]) -> None:
    trade_count = int(report.get("trade_count", 0) or 0)
    summary_lines = list(report.get("summary_lines", []))
    rows = report.get("rows", [])
    executed_at = str(report.get("executed_at", ""))

    st.subheader("🚀 実行結果")
    st.caption(f"実行時刻 (UTC): {executed_at}")
    if trade_count <= 0:
        st.info("注文は発生しませんでした。ポジションは現状維持です。")
        return

    st.success("\n".join(f"- {line}" for line in summary_lines))
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _refresh_market_prices(
    symbols: list[str],
    host: str,
    port: int,
    fallback_prices: dict[str, float],
) -> tuple[dict[str, float], str]:
    prices, refresh_at = _fetch_live_prices(symbols, host, port, fallback_prices)
    st.session_state["price_map"] = prices
    st.session_state["price_refreshed_at"] = refresh_at
    for symbol, price in prices.items():
        _append_price_history(symbol, refresh_at, price)
    return prices, refresh_at


def _current_prices_for_symbols(
    symbols: list[str],
    opend_host: str,
    opend_port: int,
) -> tuple[dict[str, float], str]:
    fallback_prices = st.session_state.get("price_map")
    if not isinstance(fallback_prices, dict):
        fallback_prices = _default_prices(symbols)
    for symbol in symbols:
        fallback_prices.setdefault(symbol, 100.0)
    fallback_prices = {symbol: float(fallback_prices[symbol]) for symbol in symbols}

    try:
        return _refresh_market_prices(symbols, opend_host, opend_port, fallback_prices)
    except Exception as exc:
        if not st.session_state.get("price_map"):
            st.warning(f"OpenDから価格取得できないため前回値/初期値を使用します: {exc}")
        else:
            st.info(f"OpenD価格更新に失敗したため前回値を維持します: {exc}")
        st.session_state["price_map"] = fallback_prices
        timestamp = (
            st.session_state.get("price_refreshed_at")
            or datetime.now(timezone.utc).isoformat()
        )
        for symbol, price in fallback_prices.items():
            _append_price_history(symbol, timestamp, price)
        return fallback_prices, timestamp


def _render_symbol_detail(
    selected_symbol: str,
    simulator: PaperSimulator,
    prices: dict[str, float],
    watch_symbols: list[str],
    refresh_at: str,
    opend_host: str,
    opend_port: int,
) -> None:
    position = simulator.positions.get(selected_symbol)
    live_price = float(
        prices.get(selected_symbol, position.avg_cost if position else 0.0)
    )
    if position is None:
        quantity = 0.0
        avg_cost = live_price
        unrealized = 0.0
        market_value = 0.0
    else:
        quantity = position.quantity
        avg_cost = position.avg_cost
        market_value = quantity * live_price
        unrealized = (live_price - avg_cost) * quantity

    st.title(f"📈 {selected_symbol} リアルタイムチャート")
    top_left, top_right = st.columns([1, 3])
    with top_left:
        if st.button("← ポートフォリオに戻る", width="stretch"):
            _close_symbol_detail()
        st.caption(f"最終更新 (UTC): {refresh_at}")
        st.metric("現在価格", f"${live_price:,.2f}")
        st.metric("保有数量", f"{quantity:,.4f} 株")
        st.metric("平均取得価格", f"${avg_cost:,.2f}")
        st.metric("時価", f"${market_value:,.2f}")
        st.metric("含み損益", f"${unrealized:,.2f}")

    with top_right:
        st.subheader("リアルタイム価格推移")
        _seed_price_history_if_needed(selected_symbol, opend_host, opend_port)
        chart_frame = _price_history_frame(selected_symbol)
        if chart_frame.empty:
            st.info("まだチャートデータがありません。数秒待つと自動で蓄積されます。")
        else:
            chart_frame["avg_cost"] = avg_cost
            st.line_chart(
                chart_frame.set_index("timestamp")[["price", "avg_cost"]],
                width="stretch",
            )

    st.divider()
    st.subheader("他の保有銘柄")
    other_symbols = [
        symbol
        for symbol in watch_symbols
        if symbol != selected_symbol and symbol in simulator.positions
    ]
    if not other_symbols:
        st.info("他の保有銘柄はありません。")
    else:
        for symbol in other_symbols:
            with st.container(border=True):
                symbol_position = simulator.positions[symbol]
                symbol_price = float(prices.get(symbol, symbol_position.avg_cost))
                cols = st.columns([2.0, 1, 1, 1])
                cols[0].markdown(f"**{symbol}**")
                cols[1].metric("数量", f"{symbol_position.quantity:,.4f}")
                cols[2].metric("価格", f"${symbol_price:,.2f}")
                cols[3].button(
                    "チャートを開く",
                    key=f"detail-open-{symbol}",
                    width="stretch",
                    on_click=_open_symbol_detail,
                    args=(symbol,),
                )


def _render_portfolio_cards(
    positions_df: pd.DataFrame,
    simulator: PaperSimulator,
    prices: dict[str, float],
) -> None:
    if positions_df.empty:
        st.info("保有ポジションはありません。")
        return

    for _, row in positions_df.iterrows():
        symbol = str(row["銘柄"])
        with st.container(border=True):
            cols = st.columns([1.4, 1, 1, 1, 1, 0.9])
            cols[0].markdown(f"**{symbol}**")
            cols[1].metric("数量", f"{float(row['数量']):,.4f}")
            cols[2].metric("現在価格", f"${float(row['現在価格']):,.2f}")
            cols[3].metric("時価", f"${float(row['時価']):,.2f}")
            cols[4].metric("含み損益", f"${float(row['含み損益']):,.2f}")
            cols[5].button(
                "チャートを開く",
                key=f"portfolio-detail-{symbol}",
                width="stretch",
                on_click=_open_symbol_detail,
                args=(symbol,),
            )


@st.fragment(run_every=f"{AUTO_REFRESH_SECONDS}s")
def _render_live_workspace(
    *,
    state_path_text: str,
    initial_cash: float,
    opend_host: str,
    opend_port: int,
    symbols_text: str,
) -> None:
    symbols = [s.strip().upper() for s in symbols_text.split(",") if s.strip()]
    if not symbols:
        symbols = list(DEFAULT_SYMBOLS)

    simulator = _load_simulator(Path(state_path_text), float(initial_cash))
    selected_symbol = _current_detail_symbol()
    watch_symbols = list(
        dict.fromkeys(
            [
                *symbols,
                *_portfolio_symbols(simulator),
                *([selected_symbol] if selected_symbol else []),
            ]
        )
    )

    prices, refresh_at = _current_prices_for_symbols(
        watch_symbols, opend_host, opend_port
    )
    live_snapshot = _portfolio_snapshot(simulator, prices)
    _append_live_equity(
        refresh_at,
        live_snapshot["equity"],
        live_snapshot["unrealized_pnl"],
    )

    if selected_symbol:
        _render_symbol_detail(
            selected_symbol,
            simulator,
            prices,
            watch_symbols,
            refresh_at,
            opend_host,
            opend_port,
        )
        return

    latest = live_snapshot
    top_m1, top_m2, top_m3, top_m4, top_m5 = st.columns(5)
    top_m1.metric("評価額", f"${latest['equity']:,.2f}")
    top_m2.metric("現金", f"${latest['cash']:,.2f}")
    top_m3.metric("時価総額", f"${latest['market_value']:,.2f}")
    top_m4.metric("含み損益", f"${latest['unrealized_pnl']:,.2f}")
    top_m5.metric("実現損益", f"${latest['realized_pnl']:,.2f}")

    left, right = st.columns([2, 1], gap="large")

    with left:
        st.subheader("📊 価格一覧")
        price_rows = pd.DataFrame(
            [{"銘柄": s, "価格 (USD)": float(prices[s])} for s in watch_symbols]
        )
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button(
                "🔄 今すぐ市場価格を更新",
                width="stretch",
                key="refresh-market-secondary",
            ):
                try:
                    prices, refresh_at = _refresh_market_prices(
                        watch_symbols,
                        opend_host,
                        int(opend_port),
                        prices,
                    )
                    live_snapshot = _portfolio_snapshot(simulator, prices)
                    _append_live_equity(
                        refresh_at,
                        live_snapshot["equity"],
                        live_snapshot["unrealized_pnl"],
                    )
                    st.success("OpenDから市場価格を更新しました。")
                    st.rerun()
                except Exception as exc:
                    st.error(f"市場価格の更新に失敗しました: {exc}")
        with col_b:
            st.info(f"自動更新中: {AUTO_REFRESH_SECONDS}秒ごと")
        st.dataframe(price_rows, hide_index=True, width="stretch")

        st.subheader("📝 注文チケット")
        with st.form("order-form", clear_on_submit=False):
            form_col1, form_col2, form_col3, form_col4 = st.columns([2, 1, 1, 1])
            with form_col1:
                symbol = st.selectbox("銘柄", watch_symbols)
            with form_col2:
                side = st.selectbox("売買", ["BUY", "SELL"])
            with form_col3:
                quantity = st.number_input("数量 (株)", min_value=1, value=1, step=1)
            with form_col4:
                default_price = float(prices.get(symbol, 100.0))
                price = st.number_input(
                    "価格 (USD)", min_value=0.01, value=default_price, step=0.01
                )
            st.caption(
                "⚠️ 価格は銘柄変更時に自動更新されません。注文前に確認してください。"
            )
            submit = st.form_submit_button("✅ 成行注文を発注", width="stretch")

        if submit:
            try:
                trade = simulator.place_market_order(
                    symbol=symbol,
                    side=side,
                    quantity=float(quantity),
                    price=float(price),
                )
                simulator.save()
                side_ja = "買い" if trade.side == "BUY" else "売り"
                st.success(
                    f"約定 #{trade.trade_id}: {trade.symbol} {side_ja} {trade.quantity:.0f}株 @ ${trade.price:.2f}"
                )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    with right:
        st.subheader("📸 スナップショット")
        st.markdown(
            "現在の価格で評価額を更新し、画面上の損益をリアルタイムで追跡します。"
        )
        if st.button("現在評価を記録", type="primary", width="stretch"):
            simulator.mark_to_market(prices)
            simulator.save()
            st.success("スナップショットを記録しました。")
            st.rerun()

        st.divider()
        st.markdown("#### 📖 使い方")
        st.markdown(
            "1. 画面は5秒ごとに自動更新されます\n"
            "2. ポートフォリオの **チャートを開く** で詳細ページへ移動\n"
            "3. 詳細ページでは OpenD の最新価格を元にリアルタイム推移を表示\n"
            "4. 注文後はポートフォリオと取引履歴が自動で追従します"
        )

    tab_portfolio, tab_trades, tab_equity = st.tabs(
        ["📂 ポートフォリオ", "📋 取引履歴", "📉 損益曲線"]
    )

    with tab_portfolio:
        positions_df = _positions_df(simulator, prices)
        if positions_df.empty:
            st.info("保有ポジションはありません。")
        else:
            st.dataframe(positions_df, hide_index=True, width="stretch")
            st.divider()
            st.markdown("#### 銘柄チャート")
            _render_portfolio_cards(positions_df, simulator, prices)

    with tab_trades:
        trades_df = _trades_df(simulator)
        if trades_df.empty:
            st.info("取引履歴はありません。")
        else:
            st.dataframe(trades_df, hide_index=True, width="stretch")

    with tab_equity:
        live_equity_df = _live_equity_frame()
        stored_equity_df = _equity_df(simulator)
        if live_equity_df.empty and stored_equity_df.empty:
            st.info(
                "スナップショットがありません。「現在評価を記録」をクリックしてください。"
            )
        else:
            equity_chart_frame, pnl_chart_frame = _equity_chart_frames(
                live_equity_df,
                stored_equity_df,
            )
            col_equity, col_pnl = st.columns(2)
            with col_equity:
                st.subheader("評価額")
                st.line_chart(equity_chart_frame, width="stretch")
            with col_pnl:
                st.subheader("含み損益")
                st.line_chart(pnl_chart_frame, width="stretch")


def main() -> None:
    settings = get_settings()

    st.set_page_config(
        page_title="ペーパートレード スタジオ",
        page_icon="📈",
        layout="wide",
    )
    _apply_styles()

    st.title("📈 ペーパートレード スタジオ")
    st.caption(
        "ローカル約定シミュレータ。価格表示とポートフォリオ表示は OpenD スナップショットで5秒ごとに更新されます。"
    )
    st.markdown(
        "<div class='app-note'>💡 価格はOpenDのスナップショットを参照します。注文価格はチケットで上書き可能です。</div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("⚙️ 設定")
        default_path = str(Path.home() / ".moomoo_bot" / "paper-sim-state.json")
        state_path_text = st.text_input("ステートファイル", value=default_path)
        initial_cash = st.number_input(
            "初期資金 (USD)",
            min_value=100.0,
            value=100_000.0,
            step=1_000.0,
        )
        opend_host = st.text_input("OpenD ホスト", value=settings.opend_host)
        opend_port = st.number_input(
            "OpenD ポート",
            min_value=1,
            max_value=65535,
            value=int(settings.opend_port),
            step=1,
        )
        symbols_text = st.text_input(
            "ウォッチリスト",
            value=", ".join(DEFAULT_SYMBOLS),
            help="カンマ区切りで入力 例: US.AAPL, US.MSFT",
        )
        st.caption("例: US.AAPL, US.MSFT, US.NVDA")
        st.divider()
        st.subheader("🔄 操作")
        st.caption(f"自動更新: {AUTO_REFRESH_SECONDS}秒ごと")
        st.caption("ポートフォリオの銘柄はチャートボタンから別ページ風に開けます。")
        if st.button("シミュレーターをリセット", type="secondary", width="stretch"):
            sim = PaperSimulator.load(
                Path(state_path_text), initial_cash=float(initial_cash)
            )
            sim.reset(initial_cash=float(initial_cash))
            st.session_state["simulator"] = sim
            st.success("シミュレーターをリセットしました。")

        st.divider()
        st.subheader("🚀 戦略実行")
        st.caption(
            "run-paper-trade.bat と同じ既定値で、ローカルシミュレータに反映します。"
        )
        execute_strategy = st.button(
            "run-paper-trade を実行", type="primary", width="stretch"
        )

        if execute_strategy:
            with st.spinner("run-paper-trade 相当の戦略を実行中..."):
                try:
                    report = _run_strategy_from_ui(
                        settings=settings,
                        state_path_text=state_path_text,
                        initial_cash=float(initial_cash),
                    )
                except Exception as exc:
                    st.session_state["last_strategy_run_error"] = str(exc)
                    st.session_state.pop("last_strategy_run_report", None)
                    st.error(f"戦略実行に失敗しました: {exc}")
                else:
                    st.session_state["last_strategy_run_error"] = None
                    st.session_state["last_strategy_run_report"] = report
                    st.rerun()

    last_strategy_run_error = st.session_state.get("last_strategy_run_error")
    last_strategy_run_report = st.session_state.get("last_strategy_run_report")
    if last_strategy_run_error:
        st.error(f"戦略実行エラー: {last_strategy_run_error}")
    elif isinstance(last_strategy_run_report, dict):
        _render_execution_report(last_strategy_run_report)

    _render_live_workspace(
        state_path_text=state_path_text,
        initial_cash=float(initial_cash),
        opend_host=opend_host,
        opend_port=int(opend_port),
        symbols_text=symbols_text,
    )


if __name__ == "__main__":
    main()
