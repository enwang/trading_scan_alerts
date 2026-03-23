# Intraday Premarket Reversal Scan

This scanner runs an intraday reversal scan and sends an alert when all of these are true:

- the stock had a large premarket drawdown versus the previous close
- it rebounds strongly during the regular session
- it is trading close to the previous day's close or previous day's high

It uses Polygon minute bars for the setup check and can also build a candidate list automatically from a market-wide Polygon prefilter.

## Setup

1. Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

2. Create a local `.env` file (secrets only):

```dotenv
POLYGON_API_KEY=your-key
```

Public/default runtime settings are now in code (watchlist refresh, thresholds, polling, prefilter, etc). Override any of them in `.env` only when needed.

3. Optional Telegram alert:

```dotenv
TELEGRAM_BOT_TOKEN=123456789:your-bot-token
TELEGRAM_CHAT_ID=123456789
```

To get `TELEGRAM_CHAT_ID`, send a message to your bot first, then open:

```text
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

4. Optional generic webhook alert:

```dotenv
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
```

If neither Telegram nor webhook is set, the script prints alerts to stdout only.

## Run

```bash
python3 scan_reversal_alert.py
```

For the separate undercut-and-rally scanner:

```bash
python3 scan_undercut_rally_alert.py
```

To inspect the current market-wide prefilter candidates without sending alerts:

```bash
python3 scan_reversal_alert.py --show-prefilter
```

## TradingView Automation

This workspace also includes a browser automation helper for TradingView:

```bash
npm run tv:login
npm run tv:watchlist
npm run tv:screener
npm run tv:lists
```

For desktop-app debugging on macOS, there is also a small UI automation helper:

```bash
npm run tv:ui -- activate
npm run tv:ui -- window_title
npm run tv:ui -- keycode 125
npm run tv:ui -- scroll -8
npm run tv:ui -- next_row
npm run tv:ui -- prev_row
npm run tv:ui -- page_down
npm run tv:ui -- open_symbol_search
npm run tv:ui -- screenshot /tmp/tradingview.png
```

Notes:

- this uses `osascript` and macOS Accessibility permissions
- you may need to allow Terminal access in `System Settings > Privacy & Security > Accessibility`
- screenshots can be used together with the scraper to debug what is actually visible in the TradingView app

How it works:

- `tv:login` opens a dedicated local Chrome profile stored in `.tradingview-profile`
- you log in to TradingView once in that browser window
- later runs reuse that saved TradingView session headlessly
- outputs are saved into `tv-output/watchlist.json` and `tv-output/screener.json`
- `npm run tv:lists` saves all watchlists plus the current screener into `tv-output/all-lists.json`

Important:

- this does not use your normal Chrome profile directly
- the selectors are best-effort and may need adjustment based on your TradingView layout

To feed the reversal scanner automatically from your TradingView watchlist after a one-time login bootstrap:

1. Run `npm run tv:login`
2. Log in to TradingView in the opened Chrome window, then press Enter in the terminal
3. Run `python3 scan_reversal_alert.py` (watchlist refresh is enabled by default)
4. Optional: set `TRADINGVIEW_WATCHLIST_ENABLED=false` in `.env` to disable automatic watchlist refresh

Once that is done, `python3 scan_reversal_alert.py` will merge symbols from:

- `REVERSAL_SCAN_LIST`
- the saved TradingView watchlist
- the Polygon prefilter, if enabled

## Default rule

The alert triggers when:

- premarket low is down at least `6%` from previous close
- current price is up at least `4%` from the premarket low
- current price is up at least `3%` from the regular-session open
- current price is within `1.5%` of previous close or previous high
- current price is still near the intraday regular-session high

## Candidate list

The reversal scanner now supports two candidate sources:

- `REVERSAL_SCAN_LIST`: your manual list for this specific rule
- Polygon prefilter: a market-wide list of names with a large drawdown

The runtime candidate set is the union of both. If you want the scanner to use only your manual list, set:

```dotenv
REVERSAL_PREFILTER_ENABLED=false
```

During premarket and postmarket, the prefilter uses current extended-hours price versus previous close. During regular hours, it uses the current day's low versus previous close so stocks that were hit hard before the open can stay on the reversal candidate list.

Important: full-market premarket and postmarket scanning requires Polygon snapshot access. If your Polygon key cannot access the market-wide snapshot endpoint, the scanner will not fall back to grouped daily data outside regular hours because that would be stale.

The scanner sends one alert per symbol per day and stores dedupe state in `alert_state.json`.

## U&R rule

`scan_undercut_rally_alert.py` is a separate scanner for `undercut and Rally` / `U&R`.

Default behavior:

- reads TradingView watchlists from `tv-output/all-lists.json`
- scans watchlists `Focus`, `Strong`, and `Next`
- also includes symbols from the `IDEA` and `HOLDING` sections inside the `Holding` watchlist
- looks for symbols that trade below the previous trading day's low
- then alerts once the minute high is at least `2%` above the lowest price reached after that undercut
- can alert again the same day only if the symbol sets a fresh lower intraday low and then rallies again
- alert timestamps are printed in `PT` by default
- stores alert state in `undercut_rally_alert_state.json`

Optional overrides:

```dotenv
TRADINGVIEW_WATCHLISTS_REFRESH_COMMAND=npm run tv:lists
UR_HOLDING_WATCHLIST_NAME=Holding
UR_HOLDING_SECTION_NAMES=IDEA,HOLDING
UR_WATCHLIST_NAMES=Focus,Strong,Next
UR_REBOUND_PCT=2.0
UR_ALERT_STATE_PATH=undercut_rally_alert_state.json
```

## Ubuntu VM refresh job

For a Linux or Ubuntu VM, the TradingView list refresh is not installed automatically. After deploying the repo, install a cron entry on the VM.

The helper script [scripts/refresh_tv_lists.sh](/Users/welsnake/trading_scan/scripts/refresh_tv_lists.sh) is portable and:

- resolves the repo path from the script location
- finds `python3` and `npm` from `PATH`
- supports `nvm` if `NVM_DIR` is set
- skips weekends and NYSE market holidays via `scripts/is_trading_day.py`

Example cron entry for `4:00 AM EST (1:00 AM PST)` server time:

```cron
0 4 * * * /path/to/repo/scripts/refresh_tv_lists.sh
```

Example with explicit repo and tool paths:

```cron
0 4 * * * SCAN_DIR=/home/ubuntu/trading_scan NVM_DIR=/home/ubuntu/.nvm /home/ubuntu/trading_scan/scripts/refresh_tv_lists.sh
```
