from __future__ import annotations

import pytest

from moomoo_bot.money import convert_capital_to_usd


def test_convert_capital_to_usd_converts_jpy() -> None:
    assert convert_capital_to_usd(100_000.0, "JPY", 150.0) == pytest.approx(666.67)


def test_convert_capital_to_usd_accepts_usd() -> None:
    assert convert_capital_to_usd(1_000.0, "USD", 150.0) == pytest.approx(1_000.0)


def test_convert_capital_to_usd_uses_live_fx_rate_input() -> None:
    assert convert_capital_to_usd(100_000.0, "JPY", 200.0) == pytest.approx(500.0)


def test_convert_capital_to_usd_rejects_unknown_currency() -> None:
    with pytest.raises(ValueError, match="unsupported capital currency"):
        convert_capital_to_usd(1_000.0, "EUR", 150.0)
