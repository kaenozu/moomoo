#!/bin/bash
set -euo pipefail

# Paper trade runner for the current validated release profile.
# This launcher pins the release defaults so local .env drift does not silently
# move the paper-trading profile back to an older setup.

cd "$(dirname "$0")"

if [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON_BIN=".venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  echo "Unable to find a Python executable under .venv" >&2
  exit 1
fi

export MOOMOO_BOT_EXECUTION_MODE=paper
export MOOMOO_BOT_CAPITAL_CURRENCY=JPY
export MOOMOO_BOT_LOOKBACK_DAYS=252
export MOOMOO_BOT_TREND_DAYS=252
export MOOMOO_BOT_TOP_N=2
export MOOMOO_BOT_SKIP_DAYS=0
export MOOMOO_BOT_REBALANCE_DAYS=21
export MOOMOO_BOT_SATELLITE_WEIGHT=0.45
export MOOMOO_BOT_TRANSACTION_COST_BPS=2.0
export MOOMOO_BOT_MAX_DAILY_ORDERS=12

CAPITAL="${CAPITAL:-100000}"
HISTORY_DAYS="${HISTORY_DAYS:-2200}"
FX_JPY_PER_USD="${FX_JPY_PER_USD:-}"

if [ -z "$FX_JPY_PER_USD" ]; then
  "$PYTHON_BIN" -m moomoo_bot paper-trade --capital "$CAPITAL" --history-days "$HISTORY_DAYS" --minimum-order-value 5.0
else
  "$PYTHON_BIN" -m moomoo_bot paper-trade --capital "$CAPITAL" --history-days "$HISTORY_DAYS" --fx-jpy-per-usd "$FX_JPY_PER_USD" --minimum-order-value 5.0
fi