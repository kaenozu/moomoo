import pandas as pd
import pytest
from moomoo_bot.strategy.momentum import MomentumRotationStrategy, MomentumRotationConfig

def test_momentum_rotation_insufficient_history():
    config = MomentumRotationConfig(lookback_days=10, trend_days=10)
    strategy = MomentumRotationStrategy(config)
    prices = pd.DataFrame(
        {'AAPL': [100.0] * 20}, 
        index=pd.date_range("2025-01-01", periods=20)
    )
    as_of = prices.index[4] # 5日分だけ
    
    decision = strategy.decide(prices, as_of)
    assert decision.reason == "insufficient_history"

def test_momentum_rotation_no_symbols():
    config = MomentumRotationConfig(lookback_days=5, trend_days=5)
    strategy = MomentumRotationStrategy(config)
    # 傾向より価格が低いデータを作成
    prices = pd.DataFrame({'AAPL': [50.0] * 20}, index=pd.date_range("2025-01-01", periods=20))
    as_of = prices.index[-1]
    
    decision = strategy.decide(prices, as_of)
    assert decision.reason == "no_symbols_above_trend"
