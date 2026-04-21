"""Broker adapter module.

Purpose: Provide Moomoo OpenD and paper trade client adapters.
Related: broker/opend.py, broker/paper.py.
"""

from .opend import MoomooOpenDClient, combine_price_series
from .paper import MoomooPaperTradeClient

__all__ = ["MoomooOpenDClient", "MoomooPaperTradeClient", "combine_price_series"]
