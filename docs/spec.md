# Spec: Moomoo US Stock CUI Bot

## Objective
Build a command-line trading bot that connects to Moomoo OpenD, runs US-stock strategies, and starts in paper trading mode. The first version should support strategy research and backtesting against a broad-market benchmark such as VT or ACWI, then paper-trade execution. Live trading is a later toggle, not the default.

## Assumptions I'm Making
1. v1 is paper-trading-first.
2. The bot targets US stocks and ETFs only.
3. Moomoo API access is through OpenD and the official Python SDK.
4. Strategy outperformance against ACWI/VT is a research goal, not a guaranteed result.
5. The bot runs as a local CUI tool on Windows first.

## Tech Stack
- Python 3.12
- moomoo-api
- Typer for CLI commands
- Pydantic and pydantic-settings for configuration
- pandas and numpy for data handling and strategy research
- Rich for terminal output
- pytest for tests

## Commands
- Install: `python -m pip install -e .[dev]`
- Run CLI: `moomoo-bot --help`
- Check setup: `moomoo-bot status`
- Research candidates: `moomoo-bot research --history-days 2200`
- Satellite blends: `moomoo-bot satellite --history-days 2200 --satellite-weights 0.05,0.10,0.15,0.20,0.25`
- Paper trading plan: `moomoo-bot paper-run --history-days 2200`
- Paper trade execution: `moomoo-bot paper-trade --history-days 2200`
- Backtest: `moomoo-bot backtest --symbols US.AAPL,US.MSFT,US.NVDA --benchmark US.VT`
- Real-data verification: `moomoo-bot verify-api --history-days 2200`
- Test: `pytest`

Planned next increment:
- Live order routing behind an explicit live-mode flag and confirmation gate after paper-trade is exercised

## Research Outcome
- The current cost-aware robustness leader is a monthly long-only momentum rotation using liquid US large-cap stocks.
- Best configuration in the 2026-04-24 OpenD rerun: lookback 252 days, trend 252 days, skip 0 days, rebalance every 21 trading days, top 2 holdings.
- The previously cited 252/252/21 top-1 setup no longer survives the newer walk-forward and regime checks; in the latest rerun it fell to rank 21 with negative test excess.
- The latest VT satellite rerun favors roughly 43% active / 57% VT in the refined sweep, with 45% active / 55% VT winning the coarse 5% grid.
- The older 23% to 25% sleeve still adds value, but it is no longer the best-balanced setting under the current ranking.
- Pure active still maximizes full-sample excess in some configurations, but it gives up robustness and is not the default recommendation.

## Project Structure
- `src/moomoo_bot/` - Application package
- `src/moomoo_bot/cli.py` - Command-line entry point
- `src/moomoo_bot/config.py` - Settings and environment parsing
- `src/moomoo_bot/broker/` - Moomoo API adapter and order routing
- `src/moomoo_bot/strategy/` - Strategy interfaces and implementations
- `src/moomoo_bot/backtest/` - Backtest engine and benchmark comparison
- `tests/` - Unit tests
- `docs/` - Project notes and research

## Code Style
- Keep functions small and explicit.
- Favor dataclasses and typed interfaces for strategy and broker boundaries.
- Keep CLI output concise and readable.
- Example:

```python
@dataclass(frozen=True)
class TradeSignal:
    symbol: str
    action: str
    target_weight: float
```

## Testing Strategy
- Use pytest for unit tests.
- Test strategy logic without the broker first.
- Mock the Moomoo SDK at the adapter boundary.
- Add integration tests only after the paper-trading path is stable.

## Boundaries
- Always: validate configuration, keep secrets out of source control, default to paper trading, and log every order decision.
- Ask first: enabling live trading by default, changing broker credentials handling, or adding new external integrations.
- Never: commit secrets, claim a strategy guarantees returns, or send live orders without an explicit live-trading flag.

## Success Criteria
- The CLI starts and shows help.
- The app can load configuration from environment variables.
- A backtest path exists for comparing strategy results with a benchmark.
- Paper-trading execution path is implemented before live trading.
- Strategy modules are swappable without changing the broker layer.

## Open Questions
- Which exact strategy family should become the default after the first paper-trading slice?
- Which benchmark should be treated as the primary comparison: VT, ACWI, or both?
- Will the bot need scheduling/daemon mode in v1, or is manual CUI execution enough for now?
