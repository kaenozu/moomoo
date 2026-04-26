"""Quantity rounding utilities module.

Purpose: Round fractional share quantities toward zero for order sizing.
Related: paper.py, risk.py, orchestrator.py.
"""

from math import floor


def floor_quantity(quantity: float, precision: float = 1000.0) -> float:
    """Round toward zero using the requested fractional-share precision."""
    normalized_precision = _normalize_precision(precision)
    normalized_quantity = (
        floor(abs(float(quantity)) * normalized_precision + 1e-9)
        / normalized_precision
    )
    return float(normalized_quantity) if normalized_quantity > 0 else 0.0


def round_quantity_toward_zero(quantity: float, precision: float = 1000.0) -> float:
    """Round toward zero, preserving sign."""
    signed_quantity = float(quantity)
    if signed_quantity == 0.0:
        return 0.0

    normalized_precision = _normalize_precision(precision)
    normalized_quantity = (
        floor(abs(signed_quantity) * normalized_precision + 1e-9)
        / normalized_precision
    )
    if normalized_quantity <= 0.0:
        return 0.0
    if signed_quantity > 0.0:
        return float(normalized_quantity)
    return float(-normalized_quantity)


def _normalize_precision(precision: float) -> float:
    normalized_precision = float(precision)
    if normalized_precision <= 0.0:
        raise ValueError("precision must be positive")
    return normalized_precision
