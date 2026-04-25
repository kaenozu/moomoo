# Operations Runbook

## Startup

1. Set the OpenD host and port.
2. Leave `MOOMOO_BOT_EXECUTION_MODE=paper` for normal use.
3. Run `moomoo-bot status` and confirm the active sleeve, cost profile, daily order cap, resolved state DB path, and any validated-profile drift.
4. Use `paper-run` to inspect the rebalance plan before submitting any order.
5. Use `paper-trade` only after the plan looks correct.

## Live Trading Safety

Live trading is opt-in and requires all of the following:

- `MOOMOO_BOT_EXECUTION_MODE=live`
- `MOOMOO_BOT_ALLOW_LIVE_TRADING=true`
- `live-trade --confirm-live-trading`

The live path also applies a conservative single-position cap through `MOOMOO_BOT_LIVE_MAX_POSITION_WEIGHT`.

## Monitoring

- `auto-run` retries transient failures with backoff.
- After repeated failures, `auto-run` stops instead of looping forever.
- Risk stops still take precedence over normal rebalancing.
- If `~/.moomoo_bot/KILL_SWITCH` exists, both one-shot trading and `auto-run` halt before any broker activity.
- Risk state and equity history are persisted in `~/.moomoo_bot/paper-state.db` or `~/.moomoo_bot/live-state.db` by default, so drawdown halts survive restarts without mixing paper and live state.
- The daily loss limit is evaluated against the latest persisted equity snapshot from the prior market date.
- Normal rebalance and stop-exit submissions are capped by `MOOMOO_BOT_MAX_DAILY_ORDERS` so a broken loop cannot keep submitting non-liquidation orders all day.
- Use `moomoo-bot execution-report` to audit recent fills, fees, slippage, pending orders, and realized PnL from the persisted state DB.
- Set `MOOMOO_BOT_STATE_DB_PATH` only if you intentionally want to override the mode-specific default DB location.

## Common Checks

- Run `moomoo-bot status` before trading.
- Run `moomoo-bot execution-report` after trading or after an `auto-run` session.
- Confirm account value and market state before the first order.
- Keep secrets out of source control and environment files.
- Create `~/.moomoo_bot/KILL_SWITCH` to force an emergency stop, and remove it only after you have reviewed the cause.
- Back up or prune the resolved state DB shown by `moomoo-bot status` during long-running paper/live operation if you want to keep the history small.
