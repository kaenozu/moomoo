"""Moomoo OpenD quote client module.

Purpose: Fetch market data, snapshots, and historical prices from OpenD.
Related: broker/__init__.py, config.py.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from time import sleep
from typing import Protocol

import pandas as pd
from moomoo import AuType, KL_FIELD, KLType, OpenQuoteContext, RET_OK


_HISTORY_REQUEST_RETRIES = 3
_HISTORY_REQUEST_RETRY_DELAY_SECONDS = 0.5
_QUOTE_REQUEST_RETRIES = 3
_QUOTE_REQUEST_RETRY_DELAY_SECONDS = 0.5


class QuoteContext(Protocol):
    def request_history_kline(
        self,
        code: str,
        start: str | None = None,
        end: str | None = None,
        ktype: str = "K_DAY",
        autype: str = "qfq",
        fields: list[str] | None = None,
        max_count: int = 1000,
        page_req_key: bytes | None = None,
        extended_time: bool = False,
    ) -> tuple[int, pd.DataFrame, bytes | None]: ...

    def get_market_snapshot(
        self, code_list: Sequence[str]
    ) -> tuple[int, pd.DataFrame | str]: ...

    def get_market_state(
        self, code_list: Sequence[str]
    ) -> tuple[int, pd.DataFrame | str]: ...

    def close(self) -> None: ...


@dataclass
class MoomooOpenDClient:
    host: str = "127.0.0.1"
    port: int = 11111
    quote_context: QuoteContext | None = None

    def __post_init__(self) -> None:
        if self.quote_context is None:
            self.quote_context = OpenQuoteContext(host=self.host, port=self.port)

    def close(self) -> None:
        if self.quote_context is not None:
            self.quote_context.close()

    def fetch_market_snapshot(self, code_list: Sequence[str]) -> pd.DataFrame:
        if self.quote_context is None:
            raise RuntimeError("OpenD quote context is not initialized")
        return self._fetch_quote_frame(
            code_list, self.quote_context.get_market_snapshot, "market snapshot"
        )

    def fetch_market_state(self, code_list: Sequence[str]) -> pd.DataFrame:
        if self.quote_context is None:
            raise RuntimeError("OpenD quote context is not initialized")
        return self._fetch_quote_frame(
            code_list, self.quote_context.get_market_state, "market state"
        )

    def _fetch_quote_frame(
        self, code_list: Sequence[str], fetcher, label: str
    ) -> pd.DataFrame:
        if self.quote_context is None:
            raise RuntimeError("OpenD quote context is not initialized")

        for attempt in range(1, _QUOTE_REQUEST_RETRIES + 1):
            ret, data = fetcher(list(code_list))
            if ret == RET_OK:
                if not isinstance(data, pd.DataFrame):
                    raise RuntimeError(f"{label.title()} did not return a DataFrame")
                return data
            if attempt == _QUOTE_REQUEST_RETRIES or not _is_transient_quote_error(data):
                raise RuntimeError(f"Failed to fetch {label}: {data}")
            sleep(_QUOTE_REQUEST_RETRY_DELAY_SECONDS * attempt)
        raise RuntimeError(f"Failed to fetch {label}: exhausted retries")

    def fetch_history(
        self,
        code: str,
        start: str | None = None,
        end: str | None = None,
        max_count: int = 1000,
    ) -> pd.DataFrame:
        if self.quote_context is None:
            raise RuntimeError("OpenD quote context is not initialized")

        pages: list[pd.DataFrame] = []
        page_req_key: bytes | None = None

        while True:
            for attempt in range(1, _HISTORY_REQUEST_RETRIES + 1):
                ret, data, next_page_req_key = self.quote_context.request_history_kline(
                    code,
                    start=start,
                    end=end,
                    ktype=KLType.K_DAY,
                    autype=AuType.QFQ,
                    fields=[KL_FIELD.ALL],
                    max_count=max_count,
                    page_req_key=page_req_key,
                    extended_time=False,
                )
                if ret == RET_OK:
                    page_req_key = next_page_req_key
                    break
                if (
                    attempt == _HISTORY_REQUEST_RETRIES
                    or not _is_transient_history_error(data)
                ):
                    raise RuntimeError(
                        f"Failed to fetch historical candlesticks for {code}: {data}"
                    )
                sleep(_HISTORY_REQUEST_RETRY_DELAY_SECONDS * attempt)
            if isinstance(data, pd.DataFrame) and not data.empty:
                pages.append(data)
            if page_req_key is None:
                break

        if not pages:
            return pd.DataFrame()

        history = pd.concat(pages, ignore_index=True)
        if "time_key" not in history.columns:
            raise RuntimeError(
                f"Historical candlesticks for {code} did not include time_key"
            )
        if "close" not in history.columns:
            raise RuntimeError(
                f"Historical candlesticks for {code} did not include close"
            )

        history = history.copy()
        history["time_key"] = pd.to_datetime(history["time_key"])
        history = history.sort_values("time_key").drop_duplicates(
            subset=["time_key"], keep="last"
        )
        history = history.set_index("time_key")
        return history

    def fetch_price_panel(
        self,
        symbols: Sequence[str],
        benchmark_symbol: str,
        history_days: int = 900,
        include_benchmark_in_prices: bool = False,
    ) -> tuple[pd.DataFrame, pd.Series]:
        if not symbols:
            raise ValueError("symbols must not be empty")
        if history_days < 60:
            raise ValueError("history_days must be at least 60")

        symbol_order = _unique_preserve_order([*symbols, benchmark_symbol])
        end_date = date.today()
        start_date = end_date - timedelta(days=history_days)

        series_by_symbol: dict[str, pd.Series] = {}
        for symbol in symbol_order:
            history = self.fetch_history(
                symbol, start=start_date.isoformat(), end=end_date.isoformat()
            )
            if history.empty:
                raise RuntimeError(f"No historical data returned for {symbol}")
            if "close" not in history.columns:
                raise RuntimeError(
                    f"Historical data for {symbol} is missing close prices"
                )
            series = history["close"].rename(symbol)
            series.index = pd.to_datetime(series.index)
            series_by_symbol[symbol] = series.sort_index()

        return combine_price_series(
            series_by_symbol,
            benchmark_symbol,
            include_benchmark_in_prices=include_benchmark_in_prices,
        )


def combine_price_series(
    series_by_symbol: Mapping[str, pd.Series],
    benchmark_symbol: str,
    include_benchmark_in_prices: bool = False,
) -> tuple[pd.DataFrame, pd.Series]:
    if benchmark_symbol not in series_by_symbol:
        raise ValueError(f"benchmark symbol {benchmark_symbol} not found in series map")

    price_frame = pd.concat(series_by_symbol, axis=1, join="inner").sort_index()
    price_frame = price_frame.dropna(how="any")
    if price_frame.empty:
        raise ValueError("No overlapping dates across the requested symbols")

    benchmark = price_frame[benchmark_symbol].rename("benchmark")
    tradable = (
        price_frame.copy()
        if include_benchmark_in_prices
        else price_frame.drop(columns=[benchmark_symbol])
    )
    tradable = tradable.dropna(how="any")
    if tradable.empty:
        raise ValueError("No tradable rows remain after removing missing values")
    return tradable, benchmark.loc[tradable.index]


def _unique_preserve_order(items: Sequence[str]) -> list[str]:
    unique_items: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            unique_items.append(item)
            seen.add(item)
    return unique_items


def _is_transient_history_error(data: object) -> bool:
    message = str(data).lower()
    return "timeout" in message or "timed out" in message or "callclose" in message


def _is_transient_quote_error(data: object) -> bool:
    message = str(data).lower()
    return (
        "timeout" in message
        or "timed out" in message
        or "callclose" in message
        or "disconnect" in message
        or "network" in message
    )
