#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAN_DIR="${SCAN_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOG_FILE="${LOG_FILE:-$SCAN_DIR/tv-refresh.log}"

export PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"

if [[ -n "${NVM_DIR:-}" && -s "${NVM_DIR}/nvm.sh" ]]; then
  # Support VM setups where npm is installed via nvm and cron has a minimal PATH.
  # shellcheck source=/dev/null
  . "${NVM_DIR}/nvm.sh"
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "${NPM_BIN:-}" ]]; then
  NPM_BIN="$(command -v npm || true)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') ERROR: python3 not found in PATH" >> "$LOG_FILE"
  exit 1
fi

if [[ -z "$NPM_BIN" ]]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') ERROR: npm not found in PATH" >> "$LOG_FILE"
  exit 1
fi

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
