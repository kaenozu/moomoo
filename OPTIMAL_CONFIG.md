# 最適設定ガイド

## バックテスト結果分析

2026年4月23日のバックテスト結果（デモデータ使用）：

| lookback | trend | top_n | skip | min_hold | 年次リターン | シャープ比 | 最大ドローダウン | 取引回数 |
|----------|-------|-------|------|----------|-------------|-----------|----------------|----------|
| **63** | **126** | **3** | **21** | **0** | **11.38%** | **0.83** | **-8.95%** | 5 |
| 126 | 126 | 2 | 0 | 0 | 3.37% | 0.34 | -11.56% | 2 |
| 252 | 252 | 1 | 21 | 21 | 0.00% | 0.00 | 0.00% | 0 |
| 252 | 252 | 1 | 21 | 0 | 0.00% | 0.00 | 0.00% | 0 |

## 最良設定

バックテストの結果、以下の設定が最もパフォーマンスが良好でした：

```
lookback_days = 63
trend_days = 126
top_n = 3
skip_days = 21
min_hold_days = 0
rebalance_days = 21
```

**パフォーマンス:**
- 年次リターン: 11.38%
- シャープ比: 0.83
- 最大ドローダウン: -8.95%
- ソルティノ比: 1.31
- カルマー比: 1.27

## 設定の解説

1. **lookback_days = 63日 (約3ヶ月)**
   - モメンタム計算の参照期間
   - 短期間なので市場の変化に素早く反応
   - 252日設定よりも高いリターンを達成

2. **trend_days = 126日 (約6ヶ月)**
   - トレンドフィルターの期間
   - lookbackより長く、強いトレンドのみを考慮
   - ボラティリティを抑制

3. **top_n = 3銘柄**
   - 上位3銘柄に等ウェイト投資
   - 分散投資で個別銘柄のリスクを低減

4. **skip_days = 21日 (1ヶ月)**
   - モメンタムのリバーサルを回避
   - 短期的な逆張りを防止

5. **min_hold_days = 0**
   - 最小保有期間なし
   - 早期の売却を許可し、リスク管理

## ペーパートレード実行方法

### 1. 環境変数設定 (.env)

```bash
MOOMOO_BOT_OPEND_HOST=127.0.0.1
MOOMOO_BOT_OPEND_PORT=11111
MOOMOO_BOT_EXECUTION_MODE=paper
MOOMOO_BOT_ALLOW_LIVE_TRADING=false
MOOMOO_BOT_SYMBOLS=US.AAPL,US.MSFT,US.NVDA,US.AMZN,US.META,US.GOOGL,US.AVGO,US.ORCL,US.AMD,US.TSLA,US.LLY,US.COST,US.JPM,US.V,US.HD
MOOMOO_BOT_BENCHMARK_SYMBOL=US.VT
MOOMOO_BOT_LOOKBACK_DAYS=63
MOOMOO_BOT_TREND_DAYS=126
MOOMOO_BOT_TOP_N=3
MOOMOO_BOT_SKIP_DAYS=21
MOOMOO_BOT_REBALANCE_DAYS=21
MOOMOO_BOT_MIN_HOLD_DAYS=0
MOOMOO_BOT_INITIAL_CAPITAL=100000
MOOMOO_BOT_CAPITAL_CURRENCY=JPY
MOOMOO_BOT_FX_JPY_PER_USD=150.0
```

### 2. ペーパートレード実行

#### 単発実行
```bash
python -m moomoo_bot paper-trade
```

#### 自動実行 (15分ごとに監視)
```bash
python -m moomoo_bot auto-run
```

#### バックテスト分析
```bash
python run_backtest_analysis.py
```

## 実稼働への移行

### 前提条件

1. **十分なペーパートレード期間**
   - 少なくとも3ヶ月以上
   - 市場環境の変化を観察

2. **パフォーマンス確認**
   - シャープ比 > 0.5
   - 最大ドローダウン < -15%
   - 正の年次リターン

3. **リスク管理設定**
   ```bash
   MOOMOO_BOT_MAX_DRAWDOWN_PCT=0.15      # 最大ドローダウン 15%
   MOOMOO_BOT_STOP_LOSS_PCT=0.10         # ストップロス 10%
   MOOMOO_BOT_TAKE_PROFIT_PCT=0.20       # テイクプロフィット 20%
   MOOMOO_BOT_DAILY_LOSS_LIMIT_PCT=0.05  # 日次損失制限 5%
   ```

### ステップ

1. **モード変更**
   ```bash
   MOOMOO_BOT_EXECUTION_MODE=live
   ```

2. **実稼働許可**
   ```bash
   MOOMOO_BOT_ALLOW_LIVE_TRADING=true
   ```

3. **ポジションサイズ制限**
   ```bash
   MOOMOO_BOT_LIVE_MAX_POSITION_WEIGHT=0.35  # 単一銘柄最大35%
   ```

4. **実稼働実行 (確認必須)**
   ```bash
   python -m moomoo_bot live-trade --confirm-live-trading
   ```

## パラメータ最適化のヒント

### バックテストを改善する場合

1. **リターン向上**
   - lookback_daysを短くする (63-126日)
   - top_nを減らす (1-2銘柄)
   - skip_daysを0にする

2. **リスク低減**
   - lookback_daysを長くする (126-252日)
   - top_nを増やす (3-5銘柄)
   - min_hold_daysを設定する (5-21日)
   - skip_daysを設定する (21日)

3. **バランス重視**
   - lookback_days = 126-189
   - trend_days = 126-252
   - top_n = 2-3
   - skip_days = 21
   - min_hold_days = 0-5

## モニタリング項目

### 日次確認

1. 総資産価値
2. 日次リターン
3. 現在の保有銘柄
4. トレンドスコア

### 週次確認

1. シャープ比 (4週間)
2. 最大ドローダウン
3. 取引回数
4. 勝率

### 月次確認

1. 年次リターン
2. ベンチマークとの比較
3. パラメータの見直し

## リスク管理

### ポジション管理

- 単一銘柄最大: 資産の35%以内
- 総ポジション: 資産の100%以内
- 残高: 最小5,000 JPY (現金として保持)

### 損失制限

- 日次損失: 資産の5%で停止
- 最大ドローダウン: 資産の15%で停止
- 単一銘柄損失: 10%でストップロス

### 市場環境変化

- VIX上昇時: ポジション縮小
- 急落時: リバランス延期
- 非流動性: 注文サイズ制限

## トラブルシューティング

### 問題: シャープ比が低い (0.5未満)

**原因:**
- ボラティリティが高い
- 頻繁な売買による取引コスト

**対処:**
- top_nを増やす (分散投資)
- rebalance_daysを長くする (21→42日)
- min_hold_daysを設定する

### 問題: ドローダウンが大きい (15%超)

**原因:**
- lookback_daysが短すぎる
- top_nが少なすぎる
- ストップロスが機能していない

**対処:**
- lookback_daysを長くする (63→126日)
- top_nを増やす (1→3)
- stop_loss_pctを下げる (0.10→0.05)

### 問題: 取引回数が多い

**原因:**
- rebalance_daysが短すぎる
- min_hold_daysが0
- skip_daysが0

**対処:**
- rebalance_daysを長くする (21→42日)
- min_hold_daysを設定する (5-10日)
- skip_daysを設定する (21日)

## 定期的な見直し

- **四半期ごと:** バックテスト再実行、パラメータ調整
- **半期ごと:** 銘柄リストの見直し
- **年次ごと:** 戦略の根本的な見直し

## 免責事項

過去のパフォーマンスは将来の結果を保証しません。ペーパートレードでの成功は必ずしも実稼働での成功に繋がりません。必ず十分なテストとリスク管理を行ってください。