# Real-Data Validation 2026-04-24

## Scope

- Source: Moomoo OpenD on 127.0.0.1:11111
- Universe: US.AAPL, US.MSFT, US.NVDA, US.AMZN, US.META, US.GOOGL, US.AVGO, US.ORCL, US.AMD, US.TSLA, US.LLY, US.COST, US.JPM, US.V, US.HD
- Benchmark: US.VT
- History window: 2200 calendar days
- Cost profile: 2.0 bps transaction cost, 0.0 fixed cost per trade
- Test status: `pytest -q` passed with 102 tests after the cost, tax-lot, and immediate-fill changes

## Commands

- `c:/gemini-desktop/glm5/moomoo/.venv/Scripts/python.exe -m moomoo_bot.cli verify-api --history-days 2200`
- `c:/gemini-desktop/glm5/moomoo/.venv/Scripts/python.exe -m moomoo_bot.cli research --history-days 2200 --max-results 10`
- `c:/gemini-desktop/glm5/moomoo/.venv/Scripts/python.exe -m moomoo_bot.cli satellite --history-days 2200 --max-results 20`
- A direct Python sweep over satellite weights 0.10 through 0.50 in 1% steps, plus 1.00, using the same 54-config momentum grid and the same 2 bps cost profile

## Verify-API Snapshot

| Metric | Value |
| --- | --- |
| Total return | 194.33% |
| Benchmark return | 153.29% |
| Outperformance | 41.03% |
| CAGR | 19.64% |
| Benchmark CAGR | 16.69% |
| Volatility | 17.75% |
| Sharpe | 1.10 |
| Max drawdown | -29.41% |
| Trades | 165 |

## Momentum Outcome

Current robustness leader:

| Rank | Lookback | Trend | Top N | Skip | Rebalance | Test Excess | WF Mean | WF Worst | Regime Worst | Full Excess | Test CAGR | Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 252 | 252 | 2 | 0 | 21 | 55.99% | 27.75% | -0.69% | -10.20% | 298.39% | 43.67% | 0.99 |

Previously documented candidate, now invalidated as the default claim:

| Rank | Lookback | Trend | Top N | Skip | Rebalance | Test Excess | WF Mean | WF Worst | Regime Worst | Full Excess | Test CAGR | Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 21 | 252 | 252 | 1 | 21 | 21 | -32.56% | 19.80% | -15.26% | -16.99% | 203.42% | 2.25% | 0.33 |

Interpretation:

- The old 252/252/21 top-1 story no longer holds after cost-aware walk-forward and regime validation.
- The current leader is still in the same monthly long-only momentum family, but it needs two holdings and no skip window to stay competitive out of sample.

## Satellite Outcome

Best result on the default 5% grid:

| Rank | Active | VT | Config | Test Excess | WF Mean | WF Worst | Regime Worst | Full Excess | Test CAGR | Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 45% | 55% | 252 / 252 / top 2 / skip 0 / rebalance 21 | 28.67% | 12.37% | 0.87% | -4.64% | 132.21% | 32.02% | 1.14 |

Best result on the refined 1% sweep:

| Rank | Active | VT | Config | Test Excess | WF Mean | WF Worst | Regime Worst | Full Excess | Test CAGR | Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 43% | 57% | 252 / 200 / top 2 / skip 21 / rebalance 21 | 21.51% | 11.09% | 0.87% | -4.44% | 161.20% | 28.84% | 1.09 |

Reference points used to decide whether the old README/spec claims still stand:

| Active | VT | Rank | Config | Test Excess | WF Worst | Regime Worst | Full Excess | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 23% | 77% | 82 | 252 / 200 / top 2 / skip 0 / rebalance 21 | 14.89% | 0.68% | -2.38% | 62.90% | Still positive, no longer best-balanced |
| 25% | 75% | 76 | 252 / 200 / top 2 / skip 0 / rebalance 21 | 16.15% | 0.72% | -2.59% | 68.60% | Slightly stronger than 23%, still below the new optimum |
| 45% | 55% | 10 | 252 / 252 / top 2 / skip 0 / rebalance 21 | 28.67% | 0.87% | -4.64% | 132.21% | Best result on the coarse CLI grid |
| 100% | 0% | 170 | 252 / 200 / top 2 / skip 21 / rebalance 21 | 40.97% | -0.69% | -10.20% | 425.16% | Pure active by robustness rank is no longer balanced |

Raw full-sample excess leader across the refined sweep:

| Active | VT | Rank by robustness | Config | Full Excess | WF Worst | Regime Worst | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 100% | 0% | 2041 | 252 / 200 / top 1 / skip 0 / rebalance 21 | 546.14% | -15.26% | -12.01% | Highest raw excess, but too fragile for the default recommendation |

Interpretation:

- 23% to 25% active is still a reasonable conservative sleeve, but it is no longer the best-balanced satellite choice under the current ranking.
- The current evidence supports a materially larger active sleeve, around 43% to 45%, when robustness is weighted above raw full-sample return.
- Pure active still maximizes raw return in some configurations, but its walk-forward and regime tails are worse and should not be presented as the balanced default.

## Documentation Decision

- Keep the project framed as a research and paper-trading system, not an index replacement claim.
- Replace the old 252/252/21 top-1 claim with the new 252/252 top-2 skip-0 result.
- Replace the old 23% satellite default with a 43% to 45% balanced range.
- Keep 23% to 25% and 100% active as alternative reference points, but describe them accurately as conservative and maximum-risk options rather than the current default.