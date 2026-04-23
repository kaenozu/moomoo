from math import floor


def floor_quantity(quantity: float) -> float:
    """Round toward zero at the broker's 3-decimal quantity precision."""
    normalized_quantity = floor(abs(float(quantity)) * 1000.0 + 1e-9) / 1000.0
    return float(normalized_quantity) if normalized_quantity > 0 else 0.0


def round_quantity_toward_zero(quantity: float) -> float:
    """Round toward zero, preserving sign."""
    signed_quantity = float(quantity)
    if signed_quantity == 0.0:
        return 0.0

    normalized_quantity = floor(abs(signed_quantity) * 1000.0 + 1e-9) / 1000.0
    if normalized_quantity <= 0.0:
        return 0.0
    if signed_quantity > 0.0:
        return float(normalized_quantity)
    return float(-normalized_quantity)
