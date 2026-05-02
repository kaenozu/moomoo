"""Row utilities tests."""

from __future__ import annotations

from datetime import datetime, timezone
import re

import pandas as pd
import pytest

from moomoo_bot.row_utils import (
    utc_now_iso,
    normalize_side,
    first_non_null_row_value,
    first_non_null_frame_value,
    position_quantities_from_frame,
    row_text,
    row_float,
)


class TestUtcNowIso:
    """Tests for utc_now_iso function."""
    
    def test_utc_now_iso_returns_string(self) -> None:
        """Test that utc_now_iso returns a string."""
        result = utc_now_iso()
        assert isinstance(result, str)

    def test_utc_now_iso_is_iso_format(self) -> None:
        """Test that output is ISO-8601 format."""
        result = utc_now_iso()
        # ISO-8601 format: 2026-05-01T10:00:00.000000+00:00
        iso_pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
        assert re.match(iso_pattern, result)

    def test_utc_now_iso_contains_utc_offset(self) -> None:
        """Test that output contains UTC offset."""
        result = utc_now_iso()
        # Should end with +00:00 or Z for UTC
        assert "+" in result or result.endswith("Z")

    def test_utc_now_iso_consistency(self) -> None:
        """Test that consecutive calls are close in time."""
        result1 = utc_now_iso()
        result2 = utc_now_iso()
        # Should be very close (within a few seconds)
        assert result1[:-7] == result2[:-7]  # Same day and hour


class TestNormalizeSide:
    """Tests for normalize_side function."""
    
    def test_normalize_buy_lowercase(self) -> None:
        assert normalize_side("buy") == "BUY"

    def test_normalize_buy_uppercase(self) -> None:
        assert normalize_side("BUY") == "BUY"

    def test_normalize_buy_mixed_case(self) -> None:
        assert normalize_side("Buy") == "BUY"

    def test_normalize_sell_lowercase(self) -> None:
        assert normalize_side("sell") == "SELL"

    def test_normalize_sell_uppercase(self) -> None:
        assert normalize_side("SELL") == "SELL"

    def test_normalize_sell_mixed_case(self) -> None:
        assert normalize_side("Sell") == "SELL"

    def test_normalize_with_spaces(self) -> None:
        assert normalize_side("  BUY  ") == "BUY"
        assert normalize_side("  SELL  ") == "SELL"

    def test_normalize_partial_match(self) -> None:
        """Test that SELL takes precedence in partial matches."""
        assert normalize_side("SELLING") == "SELL"
        assert normalize_side("BUYING") == "BUY"

    def test_normalize_none(self) -> None:
        """Test handling of None."""
        assert normalize_side(None) == ""

    def test_normalize_empty_string(self) -> None:
        assert normalize_side("") == ""

    def test_normalize_unknown_side(self) -> None:
        """Test unknown side value."""
        assert normalize_side("UNKNOWN") == "UNKNOWN"


class TestFirstNonNullRowValue:
    """Tests for first_non_null_row_value function."""
    
    def test_returns_first_non_null(self) -> None:
        row = pd.Series({"a": None, "b": "value", "c": "other"})
        result = first_non_null_row_value(row, ("a", "b", "c"))
        assert result == "value"

    def test_skips_nan(self) -> None:
        row = pd.Series({"a": float("nan"), "b": "value"})
        result = first_non_null_row_value(row, ("a", "b"))
        assert result == "value"

    def test_returns_first_match(self) -> None:
        row = pd.Series({"x": 1, "y": 2, "z": 3})
        result = first_non_null_row_value(row, ("x", "y", "z"))
        assert result == 1

    def test_returns_none_when_all_null(self) -> None:
        row = pd.Series({"a": None, "b": None})
        result = first_non_null_row_value(row, ("a", "b"))
        assert result is None

    def test_returns_none_for_missing_fields(self) -> None:
        row = pd.Series({"a": 1})
        result = first_non_null_row_value(row, ("x", "y", "z"))
        assert result is None

    def test_zero_is_non_null(self) -> None:
        """Test that 0 is considered non-null."""
        row = pd.Series({"a": None, "b": 0})
        result = first_non_null_row_value(row, ("a", "b"))
        assert result == 0

    def test_empty_string_is_non_null(self) -> None:
        """Test that empty string is considered non-null."""
        row = pd.Series({"a": None, "b": ""})
        result = first_non_null_row_value(row, ("a", "b"))
        assert result == ""


class TestFirstNonNullFrameValue:
    """Tests for first_non_null_frame_value function."""
    
    def test_returns_first_row_first_value(self) -> None:
        df = pd.DataFrame({"a": [None, 2], "b": ["value", "other"]})
        result = first_non_null_frame_value(df, ("a", "b"))
        assert result == "value"

    def test_returns_none_for_empty_dataframe(self) -> None:
        df = pd.DataFrame({"a": [], "b": []})
        result = first_non_null_frame_value(df, ("a", "b"))
        assert result is None

    def test_returns_none_for_non_dataframe(self) -> None:
        result = first_non_null_frame_value([1, 2, 3], ("a",))
        assert result is None

    def test_returns_none_for_none_input(self) -> None:
        result = first_non_null_frame_value(None, ("a",))
        assert result is None


class TestPositionQuantitiesFromFrame:
    """Tests for position_quantities_from_frame function."""
    
    def test_basic_positions(self) -> None:
        df = pd.DataFrame({
            "code": ["AAPL", "MSFT", "GOOGL"],
            "qty": [100.0, 50.5, 25.25],
        })
        result = position_quantities_from_frame(df)
        assert result == {"AAPL": 100.0, "MSFT": 50.5, "GOOGL": 25.25}

    def test_ignores_zero_quantities(self) -> None:
        df = pd.DataFrame({
            "code": ["AAPL", "MSFT", "GOOGL"],
            "qty": [100.0, 0.0, 25.0],
        })
        result = position_quantities_from_frame(df)
        assert result == {"AAPL": 100.0, "GOOGL": 25.0}
        assert "MSFT" not in result

    def test_ignores_negative_quantities(self) -> None:
        df = pd.DataFrame({
            "code": ["AAPL", "MSFT"],
            "qty": [100.0, -50.0],
        })
        result = position_quantities_from_frame(df)
        assert result == {"AAPL": 100.0}

    def test_ignores_missing_code(self) -> None:
        df = pd.DataFrame({
            "code": ["AAPL", "", "GOOGL"],
            "qty": [100.0, 50.0, 25.0],
        })
        result = position_quantities_from_frame(df)
        assert result == {"AAPL": 100.0, "GOOGL": 25.0}

    def test_empty_dataframe(self) -> None:
        df = pd.DataFrame({"code": [], "qty": []})
        result = position_quantities_from_frame(df)
        assert result == {}

    def test_handles_missing_columns(self) -> None:
        # When qty column is missing, qty defaults to 0.0 which is not > 0, so no position returned
        df = pd.DataFrame({"code": ["AAPL"]})
        result = position_quantities_from_frame(df)
        assert result == {}


class TestRowText:
    """Tests for row_text function."""
    
    def test_returns_first_non_empty(self) -> None:
        row = pd.Series({"a": "", "b": "value", "c": "other"})
        result = row_text(row, "a", "b", "c")
        assert result == "value"

    def test_strips_whitespace(self) -> None:
        row = pd.Series({"a": "  hello  "})
        result = row_text(row, "a")
        assert result == "hello"

    def test_returns_empty_string_if_not_found(self) -> None:
        row = pd.Series({"a": "value"})
        result = row_text(row, "x", "y", "z")
        assert result == ""

    def test_skips_missing_columns(self) -> None:
        row = pd.Series({"a": None, "b": "value"})
        result = row_text(row, "x", "a", "b")
        assert result == "value"

    def test_converts_to_string(self) -> None:
        row = pd.Series({"a": 123})
        result = row_text(row, "a")
        assert result == "123"

    def test_skips_nan(self) -> None:
        row = pd.Series({"a": float("nan"), "b": "value"})
        result = row_text(row, "a", "b")
        assert result == "value"


class TestRowFloat:
    """Tests for row_float function."""
    
    def test_returns_first_float(self) -> None:
        row = pd.Series({"a": None, "b": 3.14, "c": 2.71})
        result = row_float(row, "a", "b", "c")
        assert result == 3.14

    def test_converts_int_to_float(self) -> None:
        row = pd.Series({"a": 42})
        result = row_float(row, "a")
        assert result == 42.0
        assert isinstance(result, float)

    def test_converts_string_to_float(self) -> None:
        row = pd.Series({"a": "3.14"})
        result = row_float(row, "a")
        assert result == 3.14

    def test_returns_none_if_not_found(self) -> None:
        row = pd.Series({"a": "value"})
        result = row_float(row, "x", "y", "z")
        assert result is None

    def test_skips_invalid_float_values(self) -> None:
        row = pd.Series({"a": "invalid", "b": 42.0})
        result = row_float(row, "a", "b")
        assert result == 42.0

    def test_handles_nan(self) -> None:
        row = pd.Series({"a": float("nan"), "b": 3.14})
        result = row_float(row, "a", "b")
        assert result == 3.14

    def test_returns_zero(self) -> None:
        """Test that 0.0 is returned (not None)."""
        row = pd.Series({"a": 0.0})
        result = row_float(row, "a")
        assert result == 0.0

    def test_returns_negative_float(self) -> None:
        row = pd.Series({"a": -3.14})
        result = row_float(row, "a")
        assert result == -3.14
