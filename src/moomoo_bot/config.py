"""Configuration module.

Purpose: Load and manage application settings from environment variables.
Related: cli.py, risk.py.
"""

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LATEST_VERIFIED_RUNTIME_PROFILE = {
    "lookback_days": 252,
    "trend_days": 252,
    "top_n": 2,
    "skip_days": 0,
    "rebalance_days": 21,
    "satellite_weight": 0.45,
    "transaction_cost_bps": 2.0,
    "max_daily_orders": 12,
}


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="MOOMOO_BOT_",
        env_file=".env",
        extra="ignore",
    )

    opend_host: str = "127.0.0.1"
    opend_port: int = Field(default=11111, ge=1, le=65535)
    execution_mode: Literal["paper", "live"] = "paper"
    state_db_path: Path | None = None
    allow_live_trading: bool = False
    live_max_position_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    symbols: str = "US.AAPL,US.MSFT,US.NVDA,US.AMZN,US.META,US.GOOGL,US.AVGO,US.ORCL,US.AMD,US.TSLA,US.LLY,US.COST,US.JPM,US.V,US.HD"
    benchmark_symbol: str = "US.VT"
    lookback_days: int = Field(default=252, ge=1)
    trend_days: int = Field(default=252, ge=1)
    top_n: int = Field(default=2, ge=1)
    skip_days: int = Field(default=0, ge=0)
    rebalance_days: int = Field(default=21, ge=1)
    min_hold_days: int = Field(default=0, ge=0)
    backtest_min_hold_days: int = Field(default=21, ge=0)
    backtest_satellite_weight: float | None = None
    backtest_top_results: int = Field(default=5, ge=1)
    initial_capital: float = Field(default=100_000.0, gt=0.0)
    capital_currency: Literal["JPY", "USD"] = "JPY"
    fx_jpy_per_usd: float = Field(default=150.0, gt=0.0)
    max_drawdown_pct: float = Field(default=0.15, ge=0.0, le=1.0)
    market_shock_drop_pct: float = Field(default=0.05, ge=0.0, le=1.0)
    stop_loss_pct: float = Field(default=0.10, ge=0.0, le=1.0)
    take_profit_pct: float = Field(default=0.20, ge=0.0, le=1.0)
    transaction_cost_per_trade: float = Field(default=0.0, ge=0.0)
    transaction_cost_bps: float = Field(default=2.0, ge=0.0)
    max_single_position_weight: float = Field(default=1.0, gt=0.0, le=1.0)
    satellite_weight: float = Field(default=0.45, ge=0.0, le=1.0)
    fallback_asset_symbol: str | None = None
    fallback_allocation: float = Field(default=0.0, ge=0.0, le=1.0)
    volatility_lookback_days: int = Field(default=21, ge=1)
    fractional_share_precision: float = Field(default=1.0, ge=1.0)
    target_volatility_pct: float = Field(default=0.15, gt=0.0)
    max_volatility_percentile: float = Field(default=1.0, gt=0.0)
    relative_strength_lookback_days: int = Field(default=0, ge=0)
    max_drawdown_reset_pct: float = Field(default=0.05, ge=0.0, le=1.0)
    daily_loss_limit_pct: float = Field(default=0.05, ge=0.0, le=1.0)
    max_daily_orders: int = Field(default=12, ge=1)
    monthly_loss_limit_pct: float = Field(default=0.15, ge=0.0, le=1.0)
    ev_lookback_trades: int = Field(default=20, ge=1)
    ev_halt_threshold: float = Field(default=0.0, ge=-1.0, le=1.0)
    ev_reduce_threshold: float = Field(default=0.0, ge=-1.0, le=1.0)
    max_slippage_bps: float = Field(default=50.0, ge=0.0)
    autopilot_history_days: int = Field(default=2200, ge=60)
    autopilot_poll_seconds: int = Field(default=900, ge=60)
    autopilot_max_consecutive_failures: int = Field(default=5, ge=1)
    autopilot_minimum_order_value: float = Field(default=5.0, ge=0.0)
    equity_retention_days: int = Field(default=365, ge=1)
    webhook_url: HttpUrl | None = None
    health_check_enabled: bool = False
    health_check_port: int = Field(default=8080, ge=1, le=65535)
    history_retries: int = Field(default=3, ge=0)
    history_retry_delay_seconds: float = Field(default=0.5, gt=0.0)
    quote_retries: int = Field(default=3, ge=0)
    quote_retry_delay_seconds: float = Field(default=0.5, gt=0.0)

    @field_validator("symbols")
    @classmethod
    def validate_symbols_format(cls, v: str) -> str:
        if not v:
            return v
        # 既にカンマ区切り文字列。空の項目を除外
        parts = [s.strip() for s in v.split(",") if s.strip()]
        if not parts:
            return v
        # Moomoo US stocks は通常 "US.AAPL" 形式。大文字ALPHANUMERIC、ドット付き。
        # より寛容に: 英大文字と数字とドットのみ、少なくとも1文字のドットを含む。
        symbol_pattern = re.compile(r'^[A-Z0-9]+(?:\.[A-Z0-9]+)+$')
        for sym in parts:
            if not symbol_pattern.match(sym):
                raise ValueError(
                    f"Invalid symbol format: '{sym}'. Expected uppercase letters/digits separated by dots (e.g. 'US.AAPL')"
                )
        return v

    @property
    def symbol_list(self) -> list[str]:
        return [symbol.strip() for symbol in self.symbols.split(",") if symbol.strip()]

    @property
    def warmup_window(self) -> int:
        return max(self.lookback_days + self.skip_days, self.trend_days) + 1


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Clear settings cache and reload from environment."""
    get_settings.cache_clear()
    return get_settings()


def describe_runtime_profile_drift(settings: Settings) -> list[str]:
    drift: list[str] = []
    for field_name, expected_value in LATEST_VERIFIED_RUNTIME_PROFILE.items():
        current_value = getattr(settings, field_name)
        if current_value == expected_value:
            continue
        drift.append(f"{field_name}={current_value} (expected {expected_value})")
    return drift


def check_settings_changed(last_mtime: float) -> tuple[bool, float]:
    """Check if the .env file has changed since last_mtime."""
    env_file = ".env"
    if not os.path.exists(env_file):
        return False, last_mtime
    mtime = os.path.getmtime(env_file)
    if mtime > last_mtime:
        return True, mtime
    return False, last_mtime
