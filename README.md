# moomoo-bot

CUI-first trading bot prototype for Moomoo US stocks and ETFs.

The initial version is paper-trading-first and includes a strategy research/backtest path. Live trading is treated as an explicit opt-in later.

## Setup

```bash
python -m pip install -e .[dev]
```

## Commands

```bash
moomoo-bot --help
moomoo-bot status
moomoo-bot execution-report --fills-limit 20 --realizations-limit 20
moomoo-bot research --history-days 2200
moomoo-bot satellite --history-days 2200 --satellite-weights 0.05,0.10,0.15,0.20,0.25
moomoo-bot paper-run --history-days 2200
moomoo-bot paper-trade --history-days 2200
moomoo-bot live-trade --history-days 2200 --confirm-live-trading
moomoo-bot auto-run --history-days 2200
moomoo-bot backtest --min-holding-days 21
moomoo-bot verify-api --history-days 2200
pytest
```

On Windows, you can also double-click a batch file from the repository root to run the bot:

- `run-paper-trade.bat` runs the paper-trade monitoring flow.
- `run-auto-run.bat` runs the dedicated auto-monitoring flow.

If you want the one-shot order submission path, run `moomoo-bot paper-trade` directly instead of the batch file.

The paper-trading path now has two steps: `paper-run` prints the target rebalance plan, and `paper-trade` submits simulate orders through OpenD.

Live trading is available only when all of these are true:

- `MOOMOO_BOT_EXECUTION_MODE=live`
- `MOOMOO_BOT_ALLOW_LIVE_TRADING=true`
- `--confirm-live-trading` is passed to `live-trade`
- `MOOMOO_BOT_LIVE_MAX_POSITION_WEIGHT` stays at a conservative level for live sizing

The live path reuses the same sizing and risk checks, but routes orders through the real trading environment instead of simulation.

Auto-run and one-shot trading now persist risk/equity state under `~/.moomoo_bot/paper-state.db` or `~/.moomoo_bot/live-state.db` by default, so paper and live risk state do not contaminate each other while drawdown halts and daily loss checks still survive process restarts.
The daily loss limit is evaluated against the most recent persisted prior-market-date equity snapshot.
Normal order flow also enforces a persisted daily order cap, so a buggy loop cannot keep submitting non-liquidation orders forever across repeated auto-run cycles.
Use `moomoo-bot execution-report` to inspect recent fills, fees, slippage, pending orders, and realized PnL from the mode-specific persisted state DB. If you need a fixed location, set `MOOMOO_BOT_STATE_DB_PATH` explicitly.

Paper-trading capital is entered in JPY by default and converted to USD for US-stock sizing using the configured JPY/USD rate. The default `100000` in the Windows batch files now means 100,000 JPY, not 100,000 USD.

You can override the conversion rate in either of these ways:

- Environment variable: `MOOMOO_BOT_FX_JPY_PER_USD=155.5`
- CLI input: `--fx-jpy-per-usd 155.5`

Backtest defaults are separated from paper-trade defaults. The backtest command now auto-searches the momentum configuration and benchmark blend, prints the best result, and lists the top candidates.

Recommended backtest command:

```bash
moomoo-bot backtest --min-holding-days 21
```

If you want a fixed blend instead of auto-search, pass `--satellite-weight 1.0` for the pure active sleeve or another weight between 0 and 1.

The current cost-aware real-data research leader is a monthly long-only momentum rotation across liquid US large-cap stocks with a 252-day lookback, 252-day trend filter, top 2 holdings, no skip window, and a 21-trading-day rebalance.
The same active sleeve also works as a satellite over VT, but the latest real-data validation no longer favors the old 23% setting. The best balanced sleeve now sits around 43% to 45% active, while 23% to 25% remains a more conservative but less competitive blend and 100% active stays a higher-return, higher-risk option.
Those validated defaults now match the runtime profile: top 2, skip 0, and a 45% active sleeve by default unless you override them explicitly.
If you are upgrading from an older setup, review your local `.env` overrides as well. Stale values there still win over the validated defaults and can silently put you back on the old profile.

## Notes

- Moomoo OpenD is required.
- Strategy outperformance against ACWI/VT is a research goal, not a guarantee.
- Secrets must stay in environment variables.
- Run `moomoo-bot status` before trading to confirm the active sleeve, cost profile, daily order cap, resolved state DB path, and any validated-profile drift match your intended setup.
- Run `moomoo-bot execution-report` after paper or live sessions to audit fills, fees, slippage, and realized PnL.
- The latest 2026-04-24 real-data validation with 2 bps transaction costs points to 252/252 top-2 skip-0 as the current robustness leader.
- The latest satellite validation favors roughly 43% to 45% active over VT for the best balanced profile. The older 23% to 25% sleeve is still positive, but it is no longer the current best result.
- See docs/real_data_validation_2026-04-24.md for the exact commands and metrics behind the current claim set.
- See [docs/operations.md](docs/operations.md) for the operational runbook.
