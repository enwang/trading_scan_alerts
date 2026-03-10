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
