#!/bin/bash
# Paper trade runner for optimal configuration
# Best backtest config: lookback=63, trend=126, top_n=3, skip=21, min_hold=0

cd "$(dirname "$0")"

# Activate virtual environment
source .venv/bin/activate

# Run paper trade with best config
python -m moomoo_bot \
  --mode paper \
  --lookback_days 63 \
  --trend_days 126 \
  --top_n 3 \
  --skip_days 21 \
  --min_hold_days 0 \
  --rebalance_days 21

deactivate