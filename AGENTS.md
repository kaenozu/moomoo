# Kilo Agent Configuration

## Project: moomoo-bot
CLI trading bot for Moomoo US paper trading and strategy research

## Key Directories
- `src/moomoo_bot/` - Main source code
- `tests/` - Test files
- `.kilo/` - Kilo CLI configuration

## Key Files
- `src/moomoo_bot/orchestrator.py` - Trading orchestration logic (run_one_shot_trade, run_auto_monitor)
- `src/moomoo_bot/state.py` - SQLite-backed state persistence
- `src/moomoo_bot/risk.py` - Risk management (drawdown, daily loss, market shock)
- `src/moomoo_bot/broker/` - Broker adapters (moomoo OpenD, paper trading)
- `src/moomoo_bot/strategy/` - Trading strategies (momentum)
- `src/moomoo_bot/cli.py` - CLI entrypoint
- `src/moomoo_bot/notify.py` - Webhook notifications
- `src/moomoo_bot/health.py` - Health check HTTP server

## Commands
- `moomoo-bot` - Main CLI
- `python -m moomoo_bot` - Alternative CLI invocation

## Testing
- `pytest` - Run all tests
- Tests use extensive mocking for broker APIs

## Environment Variables
See `.env.example` for configuration options

## Key Changes - 2026-04-24
### 1. Orchestrator Refactoring (orchestrator.py)
- Extracted common logic from `run_one_shot_trade()` and `run_auto_monitor()` into `_execute_trading_cycle()` function
- Centralized all risk checks, order submission, and state management in shared function
- `run_one_shot_trade()` and `run_auto_monitor()` are now thin wrappers around common cycle
- Eliminated ~80% code duplication (originally ~700 lines duplicated, now ~200 lines shared)
- `_normalize_order_status` imported from state.py (no local duplicate definition)
- Improved `_market_date_for_frame()` with robust pandas Timestamp/NaT handling and fallbacks

### 2. Dependency Pinning (pyproject.toml)
- Added version constraint: `moomoo-api>=1.6.2,<2.0.0`
- Prevents silent breakage from upstream SDK changes
- Matches version mentioned in project changes documentation

### 3. New Test Files
- `tests/test_notify.py` - Webhook notification tests (send_webhook, notify_rebalance, notify_risk_stop, etc.)
- `tests/test_kill_switch.py` - Kill switch file tests (is_active, activate, deactivate, path)
- `tests/test_cli_render.py` - CLI rendering tests (backtest results, execution reports, paper plans)
- All new tests use pytest with proper mocking

### 4. Shell Script Fix (run_paper_trade.sh)
- Replaced deprecated `--lookback_days` style flags with `--lookback-days`
- Replaced `--trend_days` → `--trend-days`, `--top_n` → `--top-n`, `--skip_days` → `--skip-days`, `--min_hold_days` → `--min-hold-days`, `--rebalance_days` → `--rebalance-days`
- Added comprehensive paper trading configuration flags:
  - `--daily-order-cap 12` (explicit limit)
  - `--max-drawdown-pct 0.15` (15% max drawdown)
  - `--max-drawdown-reset-pct 0.05` (5% reset threshold)
  - `--daily-loss-limit-pct 0.05` (5% daily loss limit)
  - `--market-shock-drop-pct 0.05` (5% market shock detection)
  - `--stop-loss-pct 0.10` (10% stop loss)
  - `--take-profit-pct 0.20` (20% take profit)
  - `--min-order-value 5.0` ($5 minimum order)

### 5. State Store Improvements (state.py)
- Added WAL auto-checkpointing: `PRAGMA wal_autocheckpoint=1000` for better concurrency
- Enabled foreign key constraints: `PRAGMA foreign_keys=ON`
- WAL journaling already present from previous changes

### 6. Health Check Security Note (health.py)
- Currently runs without authentication on 127.0.0.1:8080
- Recommendation: Add bearer token authentication or IP allowlist in production
- (No code changes - awareness note only)

## LSP Type Checking Notes
Several pre-existing type checking issues remain in the codebase (all existed before changes):
- pandas DataFrame/Series type narrowing in risk.py, orchestrator.py, cli_helpers.py
- moomoo-api Session/TrdEnv enum types in broker/paper.py
- SQLite optional return types in state.py (record.count() returns int|None for some queries)
- These are false positives from mypy-pandas plugin and don't affect runtime behavior

## Development Workflow
1. Make changes to source files
2. Run `pytest tests/ -xvs` for targeted test execution
3. Check linting with project-specific commands
4. Use `/local-review-uncommitted` for code review suggestions

## Configuration Best Practices
- Always use environment variables for sensitive config
- Separate paper vs live trading with different state DBs
- Enable kill switch file for emergency stops
- Monitor health check endpoint in production (add auth for production use)
- Set up webhook notifications for critical events
- Use dependency pinning to prevent unexpected breaks

