from __future__ import annotations

from types import SimpleNamespace

import pytest
from moomoo import TrdEnv


def test_market_shock_submits_generated_liquidation_orders(monkeypatch) -> None:
    from moomoo_bot.orchestrator import cycle
    from moomoo_bot.orchestrator import risk_checks

    liquidation_orders = [object()]
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        risk_checks,
        "detect_market_shock",
        lambda _series, _threshold: "market_shock:7%",
    )
    monkeypatch.setattr(
        risk_checks,
        "_halt_and_liquidate",
        lambda **_kwargs: liquidation_orders,
    )
    monkeypatch.setattr(risk_checks, "webhook_str", lambda _settings: "")
    monkeypatch.setattr(risk_checks, "_notify_risk_stop", lambda *_args, **_kwargs: None)

    def capture_liquidation(
        trade_client,
        orders,
        current_positions,
        mode_label,
        *,
        submit_orders,
        state_store,
    ) -> None:
        observed.update(
            trade_client=trade_client,
            orders=orders,
            current_positions=current_positions,
            mode_label=mode_label,
            submit_orders=submit_orders,
            state_store=state_store,
        )

    monkeypatch.setattr(cycle, "render_and_submit_risk_liquidation", capture_liquidation)

    trade_client = object()
    state_store = object()
    current_positions = {"US.AAPL": 2.0}
    result = risk_checks.check_market_shock(
        benchmark_series=object(),
        settings=SimpleNamespace(market_shock_drop_pct=0.05),
        risk_state=SimpleNamespace(peak_account_value=100_000.0),
        persistent_risk_state=object(),
        state_store=state_store,
        account_value=93_000.0,
        market_date="2026-08-29",
        current_positions=current_positions,
        latest_prices={"US.AAPL": 100.0},
        market_open=True,
        mode_label="paper",
        submit_orders=True,
        trade_client=trade_client,
    )

    assert result == (True, False)
    assert observed == {
        "trade_client": trade_client,
        "orders": liquidation_orders,
        "current_positions": current_positions,
        "mode_label": "paper",
        "submit_orders": True,
        "state_store": state_store,
    }


def test_owned_clients_stay_open_until_cycle_finally(monkeypatch, tmp_path) -> None:
    from moomoo_bot.orchestrator import cycle

    resources: dict[str, object] = {}

    class FakeQuoteClient:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            resources["quote"] = self

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            self.close()

        def close(self) -> None:
            self.closed = True

    class FakeTradeClient:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            self.trd_env = TrdEnv.SIMULATE
            resources["trade"] = self

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            self.close()

        def get_buying_power(self) -> float:
            assert not self.closed, "owned trade client was closed before trading began"
            return 1_000.0

        def close(self) -> None:
            self.closed = True

    class FakeStateStore:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            resources["state"] = self

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            self.close()

        def load_risk_state(self):
            assert not self.closed, "owned state store was closed before trading began"
            assert not resources["quote"].closed
            assert not resources["trade"].closed
            raise RuntimeError("stop after ownership check")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(cycle._orch_module, "MoomooOpenDClient", FakeQuoteClient)
    monkeypatch.setattr(cycle._orch_module, "MoomooPaperTradeClient", FakeTradeClient)
    monkeypatch.setattr(cycle._orch_module, "StateStore", FakeStateStore)
    monkeypatch.setattr(cycle._orch_module, "_is_kill_switch_active", lambda: False)
    monkeypatch.setattr(cycle, "convert_capital_to_usd", lambda *_args: 1_000.0)
    monkeypatch.setattr(cycle, "_build_monthly_strategy", lambda _settings: object())

    settings = SimpleNamespace(
        initial_capital=1_000.0,
        fx_jpy_per_usd=1.0,
        capital_currency="USD",
        opend_host="127.0.0.1",
        opend_port=11111,
        state_db_path=tmp_path / "state.db",
        execution_mode="paper",
    )

    with pytest.raises(RuntimeError, match="stop after ownership check"):
        cycle.execute_trading_cycle(
            settings=settings,
            trade_env=TrdEnv.SIMULATE,
            symbols=["US.AAPL"],
            benchmark_symbol="US.VT",
            history_days=30,
            capital=1_000.0,
            fx_jpy_per_usd=1.0,
            minimum_order_value=5.0,
            max_position_weight=1.0,
            submit_orders=False,
            auto_mode=False,
            mode_label="paper",
        )

    assert resources["quote"].closed is True
    assert resources["trade"].closed is True
    assert resources["state"].closed is True
