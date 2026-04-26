"""Strategy module.

Purpose: Strategy base class and implementations (momentum rotation).
Related: strategy/base.py, strategy/momentum.py.
"""

from .base import Strategy, TradeDecision
from .momentum import (
    CoreSatelliteStrategy,
    MomentumRotationConfig,
    MomentumRotationStrategy,
    MonthlyMomentumRotationConfig,
    MonthlyMomentumRotationStrategy,
)

__all__ = [
    "CoreSatelliteStrategy",
    "MomentumRotationConfig",
    "MomentumRotationStrategy",
    "MonthlyMomentumRotationConfig",
    "MonthlyMomentumRotationStrategy",
    "Strategy",
    "TradeDecision",
]
