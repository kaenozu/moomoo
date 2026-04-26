"""Analyze drawdown duration and recovery from verify-api backtest."""
from __future__ import annotations

import pandas as pd
from moomoo_bot.backtest import run_backtest
from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.cli_helpers import build_monthly_strategy, requires_benchmark_prices
from moomoo_bot.config import get_settings


def analyze_drawdown(equity_curve: pd.Series) -> dict:
    running_max = equity_curve.cummax()
    drawdown = equity_curve.div(running_max).sub(1.0)

    max_dd = float(drawdown.min())
    max_dd_date = drawdown.idxmin()
    peak_before_dd = running_max.loc[:max_dd_date].idxmax()

    post_trough_curve = equity_curve.loc[max_dd_date:]
    recovery_dates = post_trough_curve[
        post_trough_curve >= running_max.loc[peak_before_dd]
    ]
    recovery_date = recovery_dates.index[0] if not recovery_dates.empty else None

    dd_duration = (max_dd_date - peak_before_dd).days
    recovery_duration = (recovery_date - max_dd_date).days if recovery_date else None
    total_duration = (recovery_date - peak_before_dd).days if recovery_date else None

    # Count all drawdowns > 10%
    significant_dd = drawdown[drawdown <= -0.10]
    
    return {
        "max_drawdown": max_dd,
        "peak_date": peak_before_dd,
        "trough_date": max_dd_date,
        "recovery_date": recovery_date,
        "dd_duration_days": dd_duration,
        "recovery_days": recovery_duration,
        "total_days": total_duration,
        "significant_dd_count": len(significant_dd),
    }


def main() -> None:
    settings = get_settings()
    client = MoomooOpenDClient(host=settings.opend_host, port=settings.opend_port)
    try:
        strategy = build_monthly_strategy(settings)
        price_frame, benchmark_series = client.fetch_price_panel(
            settings.symbol_list,
            settings.benchmark_symbol,
            history_days=2200,
            include_benchmark_in_prices=requires_benchmark_prices(strategy),
        )
        result = run_backtest(price_frame, benchmark_series, strategy)
        
        analysis = analyze_drawdown(result.equity_curve)
        
        print("=" * 60)
        print("DRAWDOWN ANALYSIS")
        print("=" * 60)
        print(f"Max Drawdown:       {analysis['max_drawdown']:.2%}")
        print(f"Peak Date:          {analysis['peak_date'].date()}")
        print(f"Trough Date:        {analysis['trough_date'].date()}")
        print(f"Recovery Date:      {analysis['recovery_date'].date() if analysis['recovery_date'] else 'Not recovered'}")
        print(f"DD Duration:        {analysis['dd_duration_days']} days")
        print(f"Recovery Duration:  {analysis['recovery_days']} days" if analysis['recovery_days'] else "Recovery Duration:  N/A")
        print(f"Total (Peak->Recovery): {analysis['total_days']} days" if analysis['total_days'] else "Total: N/A")
        print(f"Significant DDs (>10%): {analysis['significant_dd_count']}")
        print("=" * 60)
    finally:
        client.close()


if __name__ == "__main__":
    main()
