"""Tests for quantities.py - fractional share rounding utilities."""
from __future__ import annotations

import pytest

from moomoo_bot.quantities import floor_quantity, round_quantity_toward_zero


# ---------------------------------------------------------------------------
# floor_quantity
# ---------------------------------------------------------------------------

def test_floor_quantity_whole_share():
    assert floor_quantity(3.0) == 3.0


def test_floor_quantity_rounds_down():
    # 1.9999 with default precision 1000 should stay as 1.999
    assert floor_quantity(1.9999) == pytest.approx(1.999, abs=1e-6)


def test_floor_quantity_truncates_to_precision():
    # precision=1 means whole shares only
    assert floor_quantity(2.7, precision=1.0) == 2.0


def test_floor_quantity_three_decimal_precision():
    # precision=1000 means up to 3 decimal places
    result = floor_quantity(1.2345, precision=1000.0)
    assert result == pytest.approx(1.234, abs=1e-6)


def test_floor_quantity_zero_returns_zero():
    assert floor_quantity(0.0) == 0.0


def test_floor_quantity_very_small_returns_zero():
    # Below 1/1000 precision → rounds to 0
    assert floor_quantity(0.0001, precision=1000.0) == 0.0


def test_floor_quantity_negative_precision_raises():
    with pytest.raises(ValueError, match="precision must be positive"):
        floor_quantity(1.0, precision=-1.0)


def test_floor_quantity_zero_precision_raises():
    with pytest.raises(ValueError, match="precision must be positive"):
        floor_quantity(1.0, precision=0.0)


def test_floor_quantity_large_value():
    result = floor_quantity(10_000.123456, precision=1000.0)
    assert result == pytest.approx(10_000.123, abs=1e-6)


def test_floor_quantity_fractional_precision_unit():
    # precision=2 means 0.5 increments
    result = floor_quantity(1.7, precision=2.0)
    assert result == pytest.approx(1.5, abs=1e-6)


# ---------------------------------------------------------------------------
# round_quantity_toward_zero
# ---------------------------------------------------------------------------

def test_round_quantity_toward_zero_positive():
    result = round_quantity_toward_zero(1.9999, precision=1000.0)
    assert result == pytest.approx(1.999, abs=1e-6)


def test_round_quantity_toward_zero_negative():
    result = round_quantity_toward_zero(-1.9999, precision=1000.0)
    assert result == pytest.approx(-1.999, abs=1e-6)


def test_round_quantity_toward_zero_zero_returns_zero():
    assert round_quantity_toward_zero(0.0) == 0.0


def test_round_quantity_toward_zero_small_negative_returns_zero():
    result = round_quantity_toward_zero(-0.0001, precision=1000.0)
    assert result == 0.0


def test_round_quantity_toward_zero_preserves_sign_positive():
    result = round_quantity_toward_zero(5.678, precision=1.0)
    assert result == 5.0


def test_round_quantity_toward_zero_preserves_sign_negative():
    result = round_quantity_toward_zero(-5.678, precision=1.0)
    assert result == -5.0


def test_round_quantity_toward_zero_three_decimal():
    result = round_quantity_toward_zero(3.14159, precision=1000.0)
    assert result == pytest.approx(3.141, abs=1e-6)


def test_round_quantity_toward_zero_negative_three_decimal():
    result = round_quantity_toward_zero(-3.14159, precision=1000.0)
    assert result == pytest.approx(-3.141, abs=1e-6)


def test_round_quantity_toward_zero_whole_share_mode():
    assert round_quantity_toward_zero(2.999, precision=1.0) == 2.0
    assert round_quantity_toward_zero(-2.999, precision=1.0) == -2.0
