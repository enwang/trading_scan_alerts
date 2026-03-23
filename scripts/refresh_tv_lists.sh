#!/bin/zsh

set -euo pipefail

PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
SCAN_DIR="/Users/welsnake/trading_scan"
LOG_FILE="$SCAN_DIR/tv-refresh.log"
PYTHON_BIN="/opt/homebrew/bin/python3"
NPM_BIN="/opt/homebrew/bin/npm"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S %Z'
}

cd "$SCAN_DIR"

trading_check="$($PYTHON_BIN scripts/is_trading_day.py 2>&1)" || {
  echo "$(timestamp) Skipping TradingView refresh: $trading_check" >> "$LOG_FILE"
  exit 0
}

echo "$(timestamp) Starting TradingView list refresh" >> "$LOG_FILE"

if "$NPM_BIN" run tv:lists >> "$LOG_FILE" 2>&1; then
  echo "$(timestamp) TradingView list refresh complete" >> "$LOG_FILE"
  exit 0
fi

status=$?
echo "$(timestamp) TradingView list refresh failed (exit $status)" >> "$LOG_FILE"
exit $status
