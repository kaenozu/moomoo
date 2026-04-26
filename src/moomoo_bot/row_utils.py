"""DataFrame row helper utilities.

Purpose: Shared helpers for extracting normalized values from pandas rows.
Related: cli_helpers.py, broker/paper.py, orchestrator.py, risk.py.
"""

from __future__ import annotations

import pandas as pd


def first_non_null_row_value(row: pd.Series, candidate_fields: tuple[str, ...]):
    """Return the first non-null value among candidate fields from a row."""
    for field in candidate_fields:
        value = row.get(field)
        if value is None:
            continue
        if pd.isna(value):
            continue
        return value
    return None


def first_non_null_frame_value(frame: pd.DataFrame, candidate_fields: tuple[str, ...]):
    """Return the first non-null value from the first row of a DataFrame."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    return first_non_null_row_value(frame.iloc[0], candidate_fields)


def position_quantities_from_frame(position_frame: pd.DataFrame) -> dict[str, float]:
    """Extract positive position quantities keyed by symbol code."""
    positions: dict[str, float] = {}
    for _, row in position_frame.iterrows():
        code = str(row.get("code", "")).strip()
        if not code:
            continue
        qty = float(row.get("qty", 0.0) or 0.0)
        if qty > 0.0:
            positions[code] = qty
    return positions


def row_text(row: pd.Series, *column_names: str) -> str:
    """Extract first non-null text value from row for given column names."""
    for col in column_names:
        if col in row and pd.notna(row[col]):
            val = str(row[col]).strip()
            if val:
                return val
    return ""


def row_float(row: pd.Series, *column_names: str) -> float | None:
    """Extract first non-null float value from row for given column names."""
    for col in column_names:
        if col in row and pd.notna(row[col]):
            try:
                return float(row[col])
            except (TypeError, ValueError):
                continue
    return None
