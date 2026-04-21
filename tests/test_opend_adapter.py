from __future__ import annotations

import pandas as pd

from moomoo import RET_OK

from moomoo_bot.broker import MoomooOpenDClient, combine_price_series


def test_combine_price_series_uses_shared_dates_and_benchmark() -> None:
    index = pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"])
    series_by_symbol = {
        "US.AAPL": pd.Series([100.0, 101.0, 102.0], index=index, name="US.AAPL"),
        "US.MSFT": pd.Series([200.0, 201.0, 202.0], index=index, name="US.MSFT"),
        "US.VT": pd.Series([50.0, 51.0, 52.0], index=index, name="US.VT"),
    }

    prices, benchmark = combine_price_series(series_by_symbol, "US.VT")

    assert list(prices.columns) == ["US.AAPL", "US.MSFT"]
    assert list(benchmark.index) == list(index)
    assert benchmark.name == "benchmark"
    assert prices.iloc[-1].to_dict() == {"US.AAPL": 102.0, "US.MSFT": 202.0}


def test_fetch_history_combines_pages_and_sorts_rows() -> None:
    fake_context = FakeQuoteContext()
    client = MoomooOpenDClient(quote_context=fake_context)

    history = client.fetch_history("US.AAPL", start="2025-01-01", end="2025-01-03")

    assert list(history.index) == list(pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]))
    assert history.loc[pd.Timestamp("2025-01-02"), "close"] == 101.0
    assert fake_context.call_count == 2
    client.close()
    assert fake_context.closed is True


def test_fetch_history_retries_transient_timeout() -> None:
    fake_context = RetryAfterTimeoutQuoteContext()
    client = MoomooOpenDClient(quote_context=fake_context)

    history = client.fetch_history("US.AAPL", start="2025-01-01", end="2025-01-03")

    assert list(history.index) == list(pd.to_datetime(["2025-01-01", "2025-01-02"]))
    assert history.loc[pd.Timestamp("2025-01-02"), "close"] == 101.0
    assert fake_context.call_count == 2
    client.close()


def test_fetch_history_raises_for_non_transient_error() -> None:
    fake_context = PermanentFailureQuoteContext()
    client = MoomooOpenDClient(quote_context=fake_context)

    try:
        client.fetch_history("US.AAPL", start="2025-01-01", end="2025-01-03")
    except RuntimeError as exc:
        assert "permission denied" in str(exc).lower()
    else:
        raise AssertionError("Expected fetch_history to raise RuntimeError")
    finally:
        client.close()


def test_fetch_market_snapshot_retries_transient_error() -> None:
    fake_context = RetryAfterTimeoutQuoteContext()
    client = MoomooOpenDClient(quote_context=fake_context)

    snapshot = client.fetch_market_snapshot(["US.AAPL"])

    assert list(snapshot["code"]) == ["US.AAPL"]
    assert fake_context.snapshot_call_count == 2
    client.close()


def test_fetch_market_state_retries_transient_error() -> None:
    fake_context = RetryAfterTimeoutQuoteContext()
    client = MoomooOpenDClient(quote_context=fake_context)

    state = client.fetch_market_state(["US.AAPL"])

    assert list(state["code"]) == ["US.AAPL"]
    assert list(state["market_state"]) == ["MORNING"]
    assert fake_context.market_state_call_count == 2
    client.close()


def test_fetch_market_state_returns_dataframe() -> None:
    fake_context = FakeQuoteContext()
    client = MoomooOpenDClient(quote_context=fake_context)

    state = client.fetch_market_state(["US.AAPL"])

    assert list(state["code"]) == ["US.AAPL"]
    assert list(state["market_state"]) == ["MORNING"]
    client.close()


class FakeQuoteContext:
    def __init__(self) -> None:
        self.call_count = 0
        self.snapshot_call_count = 0
        self.market_state_call_count = 0
        self.closed = False

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
    ) -> tuple[int, pd.DataFrame, bytes | None]:
        self.call_count += 1
        if page_req_key is None:
            return (
                RET_OK,
                pd.DataFrame(
                    {
                        "code": [code, code],
                        "time_key": ["2025-01-02", "2025-01-01"],
                        "close": [101.0, 100.0],
                    }
                ),
                b"next",
            )
        return (
            RET_OK,
            pd.DataFrame(
                {
                    "code": [code],
                    "time_key": ["2025-01-03"],
                    "close": [102.0],
                }
            ),
            None,
        )

    def get_market_snapshot(self, code_list):
        return RET_OK, pd.DataFrame({"code": list(code_list), "last_price": [1.0] * len(code_list)})

    def get_market_state(self, code_list):
        return RET_OK, pd.DataFrame({"code": list(code_list), "market_state": ["MORNING"] * len(code_list)})

    def get_market_state(self, code_list):
        return RET_OK, pd.DataFrame({"code": list(code_list), "market_state": ["MORNING"] * len(code_list)})

    def close(self) -> None:
        self.closed = True


class RetryAfterTimeoutQuoteContext(FakeQuoteContext):
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
    ) -> tuple[int, pd.DataFrame | str, bytes | None]:
        self.call_count += 1
        if self.call_count == 1:
            return 1, "NN_ProtoRet_TimeOut", None
        if self.call_count == 2:
            return (
                RET_OK,
                pd.DataFrame(
                    {
                        "code": [code, code],
                        "time_key": ["2025-01-02", "2025-01-01"],
                        "close": [101.0, 100.0],
                    }
                ),
                None,
            )
        return super().request_history_kline(
            code,
            start=start,
            end=end,
            ktype=ktype,
            autype=autype,
            fields=fields,
            max_count=max_count,
            page_req_key=page_req_key,
            extended_time=extended_time,
        )

    def get_market_snapshot(self, code_list):
        self.snapshot_call_count += 1
        if self.snapshot_call_count == 1:
            return 1, "NN_ProtoRet_TimeOut"
        return RET_OK, pd.DataFrame({"code": list(code_list), "last_price": [1.0] * len(code_list)})

    def get_market_state(self, code_list):
        self.market_state_call_count += 1
        if self.market_state_call_count == 1:
            return 1, "NN_ProtoRet_TimeOut"
        return RET_OK, pd.DataFrame({"code": list(code_list), "market_state": ["MORNING"] * len(code_list)})


class PermanentFailureQuoteContext(FakeQuoteContext):
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
    ) -> tuple[int, pd.DataFrame | str, bytes | None]:
        self.call_count += 1
        return 1, "permission denied", None