#!/usr/bin/env python
"""バックテスト分析スクリプト

使用方法:
    python run_backtest_analysis.py

前提条件:
    - OpenDサーバーが稼働中 (moomoo API)
    - .envファイルが設定済み
"""

import pandas as pd
from pathlib import Path

from moomoo_bot.broker import MoomooOpenDClient
from moomoo_bot.config import get_settings
from moomoo_bot.backtest import run_backtest, make_demo_prices
from moomoo_bot.strategy.momentum import (
    MonthlyMomentumRotationStrategy,
    MonthlyMomentumRotationConfig,
)


def backtest_with_config(
    quote_client: MoomooOpenDClient | None,
    settings,
    lookback_days: int,
    trend_days: int,
    top_n: int,
    skip_days: int,
    min_hold_days: int = 0,
) -> dict:
    """指定されたパラメータでバックテスト実行"""
    
    # 設定更新
    config = MonthlyMomentumRotationConfig(
        lookback_days=lookback_days,
        trend_days=trend_days,
        top_n=top_n,
        skip_days=skip_days,
        rebalance_days=21,
        min_hold_days=min_hold_days,
        fallback_asset_symbol=None,
    )
    strategy = MonthlyMomentumRotationStrategy(config)
    
    # データ取得
    symbols = settings.symbol_list
    benchmark = settings.benchmark_symbol
    
    if quote_client is None:
        print("Using demo data...")
        price_frame, benchmark_series = make_demo_prices(
            symbols, periods=max(lookback_days, trend_days) + 100
        )
    else:
        try:
            # 実際のデータ取得を試行
            price_frame, benchmark_series = quote_client.fetch_price_panel(
                symbols, benchmark, history_days=max(lookback_days, trend_days) + 100
            )
        except Exception as e:
            print(f"[WARNING] Data fetch error: {e}")
            print("Using demo data...")
            price_frame, benchmark_series = make_demo_prices(
                symbols, periods=max(lookback_days, trend_days) + 100
            )
    
    # バックテスト実行
    result = run_backtest(
        price_frame,
        benchmark_series,
        strategy,
        transaction_cost_per_trade=settings.transaction_cost_per_trade,
        transaction_cost_bps=settings.transaction_cost_bps,
    )
    
    return {
        "config": config,
        "result": result,
    }


def format_results(results: list[dict]) -> pd.DataFrame:
    """結果をDataFrameに変換"""
    rows = []
    for r in results:
        res = r["result"]
        cfg = r["config"]
        rows.append({
            "lookback": cfg.lookback_days,
            "trend": cfg.trend_days,
            "top_n": cfg.top_n,
            "skip": cfg.skip_days,
            "min_hold": cfg.min_hold_days,
            "total_return": res.total_return,
            "annual_return": res.cagr,
            "sharpe": res.sharpe,
            "max_dd": res.max_drawdown,
            "sortino": res.sortino,
            "calmar": res.calmar,
            "trades": res.trade_count,
            "tx_costs": res.transaction_costs,
        })
    return pd.DataFrame(rows)


def main():
    print("=" * 80)
    print("[Backtest Analysis Tool]")
    print("=" * 80)
    
    settings = get_settings()
    
    try:
        quote_client = MoomooOpenDClient(
            host=settings.opend_host, 
            port=settings.opend_port
        )
    except Exception as e:
        print(f"[ERROR] OpenD connection error: {e}")
        print("Running in demo mode...")
        quote_client = None
    
    print(f"\n[Capital] {settings.initial_capital:,.0f} {settings.capital_currency}")
    print(f"[Symbols] {len(settings.symbol_list)}")
    print(f"[Benchmark] {settings.benchmark_symbol}")
    
    # Test parameter sets
    test_configs = [
        # Default (monthly momentum)
        {"lookback_days": 252, "trend_days": 252, "top_n":1, "skip_days": 21, "min_hold_days": 0},
        
        # More conservative
        {"lookback_days": 252, "trend_days": 252, "top_n":1, "skip_days": 21, "min_hold_days": 21},
        
        # More aggressive
        {"lookback_days": 126, "trend_days": 126, "top_n": 2, "skip_days": 0, "min_hold_days": 0},
        
        # High volatility focus
        {"lookback_days": 63, "trend_days": 126, "top_n": 3, "skip_days": 21, "min_hold_days": 0},
    ]
    
    print(f"\n[Test Configurations] {len(test_configs)}")
    print("Running backtests...\n")
    
    results = []
    for i, config in enumerate(test_configs, 1):
        print(f"[{i}/{len(test_configs)}] Testing... ", end="", flush=True)
        
        try:
            result = backtest_with_config(
                quote_client if quote_client else None,
                settings,
                **config,
            )
            results.append(result)
            print("[OK]")
        except Exception as e:
            print(f"[ERROR] {e}")
    
    if not results:
        raise RuntimeError("No backtest results were produced")

    # 結果集計
    df = format_results(results)
    df = df.sort_values("annual_return", ascending=False)
    
    print("\n" + "=" * 80)
    print("[Backtest Results (Sorted by Annual Return)]")
    print("=" * 80)
    print(df.to_string(index=False))
    
    # ベスト設定の推奨
    print("\n" + "=" * 80)
    print("[Recommended Best Configurations]")
    print("=" * 80)
    
    best_sharpe = df.iloc[df["sharpe"].idxmax()]
    best_return = df.iloc[df["annual_return"].idxmax()]
    best_dd = df.iloc[df["max_dd"].idxmin()]
    
    print(f"\n[Highest Sharpe Ratio]:")
    print(f"  lookback={best_sharpe['lookback']}, trend={best_sharpe['trend']}, "
          f"top_n={best_sharpe['top_n']}, skip={best_sharpe['skip']}")
    print(f"  Annual Return: {best_sharpe['annual_return']:.2%}, "
          f"Sharpe: {best_sharpe['sharpe']:.2f}, "
          f"Max DD: {best_sharpe['max_dd']:.2%}")
    
    print(f"\n[Highest Annual Return]:")
    print(f"  lookback={best_return['lookback']}, trend={best_return['trend']}, "
          f"top_n={best_return['top_n']}, skip={best_return['skip']}")
    print(f"  Annual Return: {best_return['annual_return']:.2%}, "
          f"Sharpe: {best_return['sharpe']:.2f}, "
          f"Max DD: {best_return['max_dd']:.2%}")
    
    print(f"\n[Lowest Max Drawdown]:")
    print(f"  lookback={best_dd['lookback']}, trend={best_dd['trend']}, "
          f"top_n={best_dd['top_n']}, skip={best_dd['skip']}")
    print(f"  Annual Return: {best_dd['annual_return']:.2%}, "
          f"Sharpe: {best_dd['sharpe']:.2f}, "
          f"Max DD: {best_dd['max_dd']:.2%}")
    
    # Save results
    output_path = Path("backtest_results.csv")
    df.to_csv(output_path, index=False)
    print(f"\n[Saved] {output_path}")
    
    # Parameter tuning guide
    print("\n" + "=" * 80)
    print("[Parameter Tuning Guide]")
    print("=" * 80)
    print("""
パラメータ調整のヒント:

1. lookback_days (ルックバック期間)
   - 長いほど安定性が増す（252日 = 1年間が標準）
   - 短いほど反応が速いが、ノイズに弱い
   - 推奨: 126-252日

2. trend_days (トレンド期間)
   - 長いほど強いトレンドのみを考慮
   - lookbackと同じか短く設定
   - 推奨: 126-252日

3. top_n (選択銘柄数)
   - 少ないほど集中投資（リスク高いが期待リターン高い）
   - 多いほど分散投資（安定性高いが期待リターン低い）
   - 推奨: 1-3銘柄

4. skip_days (スキップ期間)
   - モメンタムのリバーサルを回避（21日 = 1ヶ月）
   - 0で無効化（より頻繁にリバランス）
   - 推奨: 0-21日

5. min_hold_days (最小保有期間)
   - 短期間での売買を抑制
   - 0で無効化
   - 推奨: 0-21日

リスク管理設定 (.env):
- max_drawdown_pct: 最大許容ドローダウン（デフォルト: 0.15 = 15%）
- stop_loss_pct: 個別銘柄のストップロス（デフォルト: 0.10 = 10%）
- take_profit_pct: 個別銘柄のテイクプロフィット（デフォルト: 0.20 = 20%）
- daily_loss_limit_pct: 日次損失制限（デフォルト: 0.05 = 5%）
    """)
    
    if quote_client is not None:
        quote_client.close()
    
    print("\n[Done] Backtest analysis complete")


if __name__ == "__main__":
    main()