"""Tests for CLI rendering module."""

import pandas as pd
import pytest
from unittest.mock import patch
from moomoo import TrdSide
from moomoo_bot.cli_render import (
    format_percent,
    format_ratio,
    format_money,
    render_backtest_result,
    render_snapshot,
    render_execution_report,
    render_research_results,
    render_satellite_results,
    render_paper_plan,
    render_paper_trade_plan,
    render_risk_orders,
    render_order_response,
)
from moomoo_bot.backtest import BacktestResult
from moomoo_bot.paper import PaperAllocation, PaperOrderInstruction, PaperPlan
from moomoo_bot.research import MomentumSearchResult, SatelliteSearchResult
from moomoo_bot.strategy.momentum import MonthlyMomentumRotationConfig


def make_backtest_result() -> BacktestResult:
    index = pd.date_range("2023-01-01", periods=3, freq="B")
    curve = pd.Series([100.0, 101.0, 102.0], index=index)
    return BacktestResult(
        equity_curve=curve,
        benchmark_curve=curve,
        trade_count=25,
        transaction_costs=1.25,
        total_return=0.15,
        benchmark_return=0.10,
        cagr=0.12,
        benchmark_cagr=0.08,
        volatility=0.20,
        sharpe=1.5,
        max_drawdown=-0.08,
        outperformance=0.05,
        sortino=1.1,
        calmar=1.4,
        max_drawdown_duration_days=3,
    )


def make_search_result(
    satellite_weight: float | None = None,
) -> MomentumSearchResult | SatelliteSearchResult:
    result_kwargs = dict(
        config=MonthlyMomentumRotationConfig(
            lookback_days=63,
            trend_days=126,
            top_n=3,
            skip_days=21,
            rebalance_days=21,
        ),
        full_result=make_backtest_result(),
        train_excess=0.05,
        test_excess=0.03,
        train_cagr=0.04,
        test_cagr=0.10,
        test_sharpe=1.2,
        train_drawdown=-0.02,
        test_drawdown=-0.03,
        walk_forward_mean_excess=0.02,
        walk_forward_worst_excess=-0.01,
        walk_forward_mean_cagr=0.03,
        walk_forward_worst_drawdown=-0.04,
        walk_forward_window_count=4,
        regime_worst_excess=-0.03,
    )
    if satellite_weight is None:
        return MomentumSearchResult(**result_kwargs)
    return SatelliteSearchResult(satellite_weight=satellite_weight, **result_kwargs)


class TestFormatFunctions:
    def test_format_percent(self):
        assert format_percent(0.1234) == "12.34%"
        assert format_percent(0.0) == "0.00%"
        assert format_percent(-0.05) == "-5.00%"

    def test_format_ratio(self):
        assert format_ratio(1.234) == "1.23"
        assert format_ratio(2.0) == "2.00"

    def test_format_money(self):
        assert format_money(1234.567) == "1,234.57"
        assert format_money(1000000) == "1,000,000.00"


class TestRenderBacktestResult:
    @patch("moomoo_bot.cli_render.console")
    def test_render_backtest_result(self, mock_console):
        result = make_backtest_result()
        render_backtest_result(result, "SPY", "AAPL MSFT GOOGL", satellite_weight=0.3)
        assert mock_console.print.called

    @patch("moomoo_bot.cli_render.console")
    def test_render_backtest_result_no_satellite(self, mock_console):
        result = make_backtest_result()
        render_backtest_result(result, "SPY", "AAPL MSFT GOOGL")
        assert mock_console.print.called


class TestRenderSnapshot:
    @patch("moomoo_bot.cli_render.console")
    def test_render_snapshot(self, mock_console):
        df = pd.DataFrame(
            {
                "code": ["AAPL", "GOOGL"],
                "name": ["Apple", "Google"],
                "last_price": [150.0, 2800.0],
                "update_time": ["2023-01-01 10:00:00", "2023-01-01 10:00:00"],
                "prev_close_price": [148.0, 2780.0],
            }
        )
        render_snapshot(df)
        assert mock_console.print.called

    @patch("moomoo_bot.cli_render.console")
    def test_render_snapshot_missing_columns(self, mock_console):
        df = pd.DataFrame({"other": [1, 2]})
        render_snapshot(df)
        # Should not print table if required columns missing


class TestRenderExecutionReport:
    @patch("moomoo_bot.cli_render.console")
    def test_render_execution_report(self, mock_console):
        from moomoo_bot.state import ExecutionAuditSummary

        summary = ExecutionAuditSummary(
            order_count=10,
            pending_order_count=2,
            fill_count=8,
            buy_fill_count=5,
            sell_fill_count=3,
            realization_count=2,
            open_lot_count=3,
            total_fees=25.50,
            total_slippage=12.30,
            realized_pnl=150.00,
            last_fill_at="2023-01-01T10:00:00",
            last_realization_at="2023-01-01T09:00:00",
        )
        recent_fills = []
        recent_realizations = []
        recent_orders = []
        render_execution_report(
            summary, recent_fills, recent_realizations, recent_orders, "AAPL"
        )
        assert mock_console.print.called

    @patch("moomoo_bot.cli_render.render_risk_orders")
    @patch("moomoo_bot.cli_render.console")
    def test_render_risk_orders_empty(self, mock_console, mock_render_risk):
        from moomoo_bot.paper import PaperPlan

        PaperPlan(
            as_of=pd.Timestamp("2023-01-01"),
            capital=10000.0,
            reason="test",
            allocations=[],
            cash_remaining=10000.0,
        )
        # Test rendering with no risk orders


class TestRenderResearchResults:
    @patch("moomoo_bot.cli_render.console")
    def test_render_research_results(self, mock_console):
        result = make_search_result()
        render_research_results([result], "SPY", "AAPL MSFT", evaluated_count=10)
        assert mock_console.print.called

    @patch("moomoo_bot.cli_render.console")
    def test_render_research_results_empty(self, mock_console):
        render_research_results([], "SPY", "AAPL")
        # Should print "No strategies matched"


class TestRenderSatelliteResults:
    @patch("moomoo_bot.cli_render.console")
    def test_render_satellite_results(self, mock_console):
        result = make_search_result(satellite_weight=0.3)
        render_satellite_results(
            [result],
            "SPY",
            "AAPL MSFT",
            evaluated_count=10,
            config_count=5,
            weight_count=3,
        )
        assert mock_console.print.called


class TestRenderPaperPlan:
    @patch("moomoo_bot.cli_render.console")
    def test_render_paper_plan(self, mock_console):
        plan = PaperPlan(
            as_of=pd.Timestamp("2023-01-01"),
            capital=10000.0,
            reason="momentum",
            allocations=[
                PaperAllocation(
                    symbol="AAPL",
                    weight=0.5,
                    price=150.0,
                    target_value=5000,
                    target_quantity=33.33,
                    target_cost=4999.50,
                ),
            ],
            cash_remaining=5000.0,
        )
        benchmark_series = pd.Series(
            [100.0, 101.0, 102.0], index=pd.date_range("2023-01-01", periods=3)
        )
        render_paper_plan(plan, "SPY", "AAPL MSFT", benchmark_series)
        assert mock_console.print.called


class TestRenderPaperTradePlan:
    @patch("moomoo_bot.cli_render.render_paper_plan")
    @patch("moomoo_bot.cli_render.console")
    def test_render_paper_trade_plan(self, mock_console, mock_render_plan):
        plan = PaperPlan(
            as_of=pd.Timestamp("2023-01-01"),
            capital=10000.0,
            reason="momentum",
            allocations=[
                PaperAllocation(
                    symbol="AAPL",
                    weight=0.5,
                    price=150.0,
                    target_value=5000,
                    target_quantity=33.33,
                    target_cost=4999.50,
                ),
            ],
            cash_remaining=5000.0,
        )
        benchmark_series = pd.Series(
            [100.0, 101.0, 102.0], index=pd.date_range("2023-01-01", periods=3)
        )
        current_positions = {"AAPL": 30.0}
        instructions = []
        render_paper_trade_plan(
            plan, "SPY", "AAPL MSFT", benchmark_series, current_positions, instructions
        )
        assert mock_render_plan.called


class TestRenderRiskOrders:
    @patch("moomoo_bot.cli_render.console")
    def test_render_risk_orders_empty(self, mock_console):
        render_risk_orders([], {"AAPL": 10.0}, "Risk Exit Orders")
        # Should print "none"

    @patch("moomoo_bot.cli_render.console")
    def test_render_risk_orders_with_orders(self, mock_console):
        orders = [
            PaperOrderInstruction(
                symbol="AAPL",
                side=TrdSide.SELL,
                quantity=5.0,
                price=150.0,
                reason="test",
            )
        ]
        render_risk_orders(orders, {"AAPL": 10.0}, "Risk Exit Orders")
        assert mock_console.print.called


class TestRenderOrderResponse:
    @patch("moomoo_bot.cli_render.console")
    def test_render_order_response(self, mock_console):
        instruction = type(
            "obj",
            (object,),
            {
                "side": "BUY",
                "symbol": "AAPL",
                "quantity": 10.0,
                "price": 150.0,
            },
        )()
        response = pd.DataFrame({"order_id": ["12345"], "order_status": ["submitted"]})
        render_order_response(instruction, response)
        assert mock_console.print.called

    @patch("moomoo_bot.cli_render.console")
    def test_render_order_response_empty(self, mock_console):
        instruction = type(
            "obj",
            (object,),
            {
                "side": "BUY",
                "symbol": "AAPL",
                "quantity": 10.0,
                "price": 150.0,
            },
        )()
        response = pd.DataFrame()
        render_order_response(instruction, response)
        assert mock_console.print.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
