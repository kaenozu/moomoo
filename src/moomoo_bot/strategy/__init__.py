"""Strategy module.

Purpose: Strategy base class and implementations (momentum rotation, recommendation).
Related: strategy/base.py, strategy/momentum.py, strategy/recommendation.py.
"""

from .base import Strategy, TradeDecision
from .momentum import (
    CoreSatelliteStrategy,
    MomentumRotationConfig,
    MomentumRotationStrategy,
    MonthlyMomentumRotationConfig,
    MonthlyMomentumRotationStrategy,
)
from .recommendation import RecommendationConfig, RecommendationStrategy

__all__ = [
    "CoreSatelliteStrategy",
    "MomentumRotationConfig",
    "MomentumRotationStrategy",
    "MonthlyMomentumRotationConfig",
    "MonthlyMomentumRotationStrategy",
    "RecommendationConfig",
    "RecommendationStrategy",
    "Strategy",
    "TradeDecision",
]
