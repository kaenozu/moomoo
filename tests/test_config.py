from moomoo_bot.config import describe_runtime_profile_drift, get_settings, Settings


def test_symbol_list_parsing() -> None:
    settings = Settings(symbols=" US.AAPL , US.MSFT ,, US.NVDA ")
    assert settings.symbol_list == ["US.AAPL", "US.MSFT", "US.NVDA"]


def test_warmup_window_uses_longest_lookback() -> None:
    settings = Settings(lookback_days=63, trend_days=200)
    assert settings.warmup_window == 201


def test_risk_defaults_are_conservative() -> None:
    settings = Settings()
    assert settings.max_drawdown_pct == 0.15
    assert settings.market_shock_drop_pct == 0.05
    assert settings.stop_loss_pct == 0.10
    assert settings.take_profit_pct == 0.20


def test_backtest_defaults_are_separated_from_trading_defaults() -> None:
    settings = Settings()
    assert settings.backtest_min_hold_days == 21
    assert settings.backtest_satellite_weight == -1.0
    assert settings.backtest_top_results == 5


def test_trading_defaults_follow_latest_verified_profile() -> None:
    settings = Settings(_env_file=None)

    assert settings.lookback_days == 252
    assert settings.trend_days == 252
    assert settings.top_n == 2
    assert settings.skip_days == 0
    assert settings.rebalance_days == 21
    assert settings.satellite_weight == 0.45


def test_capital_defaults_are_jpy_for_paper_trading() -> None:
    settings = Settings()
    assert settings.capital_currency == "JPY"
    assert settings.fx_jpy_per_usd == 150.0


def test_live_trading_defaults_to_disabled() -> None:
    settings = Settings()
    assert settings.allow_live_trading is False


def test_execution_safety_defaults_are_conservative() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_daily_orders == 12


def test_runtime_profile_drift_is_empty_for_latest_defaults() -> None:
    settings = Settings(_env_file=None)

    assert describe_runtime_profile_drift(settings) == []


def test_runtime_profile_drift_lists_stale_overrides() -> None:
    settings = Settings(
        _env_file=None,
        lookback_days=63,
        trend_days=126,
        top_n=3,
        skip_days=21,
    )

    drift = describe_runtime_profile_drift(settings)

    assert "lookback_days=63 (expected 252)" in drift
    assert "trend_days=126 (expected 252)" in drift
    assert "top_n=3 (expected 2)" in drift
    assert "skip_days=21 (expected 0)" in drift


def test_state_db_path_can_be_overridden_via_environment(monkeypatch) -> None:
    monkeypatch.setenv("MOOMOO_BOT_STATE_DB_PATH", "custom/live-state.db")

    settings = get_settings()

    assert str(settings.state_db_path).replace("\\", "/") == "custom/live-state.db"


def test_fx_rate_can_be_overridden_via_environment(monkeypatch) -> None:
    monkeypatch.setenv("MOOMOO_BOT_FX_JPY_PER_USD", "155.5")

    settings = get_settings()

    assert settings.fx_jpy_per_usd == 155.5


def test_live_trading_flag_can_be_overridden_via_environment(monkeypatch) -> None:
    monkeypatch.setenv("MOOMOO_BOT_ALLOW_LIVE_TRADING", "true")

    settings = get_settings()

    assert settings.allow_live_trading is True


def test_live_position_cap_can_be_overridden_via_environment(monkeypatch) -> None:
    monkeypatch.setenv("MOOMOO_BOT_LIVE_MAX_POSITION_WEIGHT", "0.25")

    settings = get_settings()

    assert settings.live_max_position_weight == 0.25
