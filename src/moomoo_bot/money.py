from __future__ import annotations


def convert_capital_to_usd(amount: float, currency: str, fx_jpy_per_usd: float) -> float:
    if amount <= 0.0:
        raise ValueError("capital must be positive")
    if fx_jpy_per_usd <= 0.0:
        raise ValueError("fx_jpy_per_usd must be positive")

    normalized_currency = currency.strip().upper()
    if normalized_currency == "USD":
        return float(amount)
    if normalized_currency == "JPY":
        return float(amount) / fx_jpy_per_usd

    raise ValueError(f"unsupported capital currency: {currency}")