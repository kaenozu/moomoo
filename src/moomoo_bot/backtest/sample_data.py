"""Sample data generation module.

Purpose: Generate random walk price data for backtest demos.
Related: backtest/engine.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def make_demo_prices(
    symbols: Sequence[str],
    periods: int = 504,
    seed: int = 7,
) -> tuple[pd.DataFrame, pd.Series]:
    if not symbols:
        raise ValueError("symbols must not be empty")
    if periods < 30:
        raise ValueError("periods must be at least 30")

    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=periods)

    price_data: dict[str, np.ndarray] = {}
    for index, symbol in enumerate(symbols):
        drift = 0.00015 + index * 0.00008
        volatility = 0.012 + index * 0.001
        shocks = rng.normal(loc=drift, scale=volatility, size=periods)
        price_data[symbol] = 100.0 * np.exp(np.cumsum(shocks))

    benchmark_shocks = rng.normal(loc=0.0002, scale=0.01, size=periods)
    benchmark = pd.Series(
        100.0 * np.exp(np.cumsum(benchmark_shocks)),
        index=dates,
        name="benchmark",
    )
    return pd.DataFrame(price_data, index=dates), benchmark
