# Operations Runbook

## Startup

1. Set the OpenD host and port.
2. Leave `MOOMOO_BOT_EXECUTION_MODE=paper` for normal use.
3. Use `paper-run` to inspect the rebalance plan before submitting any order.
4. Use `paper-trade` only after the plan looks correct.

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

## Common Checks

- Run `moomoo-bot status` before trading.
- Confirm account value and market state before the first order.
- Keep secrets out of source control and environment files.
