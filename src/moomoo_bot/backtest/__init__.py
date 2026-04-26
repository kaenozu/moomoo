"""Backtest module.

Purpose: Run backtests and generate sample price data.
Related: backtest/engine.py, backtest/sample_data.py.
"""

from .engine import (
    BacktestResult,
    WalkForwardResult,
    WalkForwardFold,
    classify_regimes,
    run_backtest,
    run_cost_stress_analysis,
    run_sensitivity_sweep,
    run_walk_forward_backtest,
)
from .sample_data import make_demo_prices

__all__ = [
    "BacktestResult",
    "WalkForwardFold",
    "WalkForwardResult",
    "classify_regimes",
    "make_demo_prices",
    "run_backtest",
    "run_cost_stress_analysis",
    "run_sensitivity_sweep",
    "run_walk_forward_backtest",
]
