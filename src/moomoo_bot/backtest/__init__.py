"""Backtest module.

Purpose: Run backtests and generate sample price data.
Related: backtest/engine.py, backtest/sample_data.py.
"""

from .engine import BacktestResult, run_backtest
from .sample_data import make_demo_prices

__all__ = ["BacktestResult", "make_demo_prices", "run_backtest"]
