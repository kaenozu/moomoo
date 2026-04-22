"""Configuration module.

Purpose: Load and manage application settings from environment variables.
Related: cli.py, risk.py.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="MOOMOO_BOT_",
        env_file=".env",
        extra="ignore",
    )

    opend_host: str = "127.0.0.1"
    opend_port: int = 11111
    execution_mode: Literal["paper", "live"] = "paper"
    allow_live_trading: bool = False
    live_max_position_weight: float = 0.35
    symbols: str = "US.AAPL,US.MSFT,US.NVDA,US.AMZN,US.META,US.GOOGL,US.AVGO,US.ORCL,US.AMD,US.TSLA,US.LLY,US.COST,US.JPM,US.V,US.HD"
    benchmark_symbol: str = "US.VT"
    lookback_days: int = 252
    trend_days: int = 252
    top_n: int = 1
    skip_days: int = 21
    rebalance_days: int = 21
    min_hold_days: int = 0
    backtest_min_hold_days: int = 21
    backtest_satellite_weight: float = -1.0
    backtest_top_results: int = 5
    initial_capital: float = 100_000.0
    capital_currency: Literal["JPY", "USD"] = "JPY"
    fx_jpy_per_usd: float = 150.0
    max_drawdown_pct: float = 0.15
    market_shock_drop_pct: float = 0.05
    stop_loss_pct: float = 0.10
    take_profit_pct: float = 0.20
    transaction_cost_per_trade: float = 0.0
    transaction_cost_bps: float = 0.0
    max_single_position_weight: float = 0.20
    fallback_asset_symbol: str | None = None
    history_retries: int = 3
    history_retry_delay_seconds: float = 0.5
    quote_retries: int = 3
    quote_retry_delay_seconds: float = 0.5

    @property
    def symbol_list(self) -> list[str]:
        return [symbol.strip() for symbol in self.symbols.split(",") if symbol.strip()]

    @property
    def warmup_window(self) -> int:
        return max(self.lookback_days + self.skip_days, self.trend_days) + 1


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
