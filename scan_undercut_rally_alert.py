#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scan_reversal_alert import (
    EASTERN,
    ENV_PATH,
    PACIFIC,
    RateLimiter,
    YFinanceClient,
    _is_regular_bar,
    _is_market_hours,
    http_json_post,
    load_dotenv,
)


_INTERVAL_LOW = 300
_INTERVAL_MEDIUM = 180
_INTERVAL_HIGH = 120
_INTERVAL_URGENT = 60


@dataclass(frozen=True)
class ScanConfig:
    tradingview_watchlists_path: Path = Path("tv-output/all-lists.json")
    tradingview_watchlists_refresh_command: str | None = None
    watchlist_names: tuple[str, ...] = ("Focus", "Strong", "Next")
    holding_watchlist_name: str = "Holding"
    holding_section_names: tuple[str, ...] = ("IDEA", "HOLDING")
    rebound_pct: float = 2.0
    api_rate_limit: int = 30
    poll_seconds: int = 60
    alert_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    alert_state_path: Path = Path("undercut_rally_alert_state.json")


@dataclass
class SymbolState:
    previous_low: float
    previous_close: float
    next_scan_at: float = 0.0
    interval: int = _INTERVAL_LOW


@dataclass(frozen=True)
class ScanResult:
    symbol: str
    previous_low: float
    previous_close: float
    undercut_low: float
    undercut_time: datetime
    trigger_price: float
    trigger_time: datetime
    rebound_from_low_pct: float


def load_config() -> ScanConfig:
    load_dotenv(ENV_PATH)

    raw_names = os.getenv("UR_WATCHLIST_NAMES", "Focus,Strong,Next").strip()
    watchlist_names = tuple(
        name.strip()
        for name in raw_names.split(",")
        if name.strip()
    )

    return ScanConfig(
        tradingview_watchlists_path=Path(
            os.getenv("TRADINGVIEW_WATCHLISTS_PATH", "tv-output/all-lists.json")
        ),
        tradingview_watchlists_refresh_command=(
            os.getenv("TRADINGVIEW_WATCHLISTS_REFRESH_COMMAND", "").strip() or None
        ),
        watchlist_names=watchlist_names or ("Focus", "Strong", "Next"),
        holding_watchlist_name=os.getenv("UR_HOLDING_WATCHLIST_NAME", "Holding").strip() or "Holding",
        holding_section_names=tuple(
            section.strip()
            for section in os.getenv("UR_HOLDING_SECTION_NAMES", "IDEA,HOLDING").split(",")
            if section.strip()
        ) or ("IDEA", "HOLDING"),
        rebound_pct=float(os.getenv("UR_REBOUND_PCT", "2.0")),
        api_rate_limit=int(os.getenv("API_RATE_LIMIT", "30")),
        poll_seconds=int(os.getenv("POLL_SECONDS", "60")),
        alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL", "").strip() or None,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip() or None,
        alert_state_path=Path(
            os.getenv("UR_ALERT_STATE_PATH", "undercut_rally_alert_state.json")
        ),
    )


def refresh_tv_watchlists(config: ScanConfig) -> None:
    if not config.tradingview_watchlists_refresh_command:
        return
    try:
        subprocess.run(
            shlex.split(config.tradingview_watchlists_refresh_command),
            check=True,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            "TradingView watchlists refresh command not found: "
            f"{config.tradingview_watchlists_refresh_command}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"TradingView watchlists refresh failed with exit code {exc.returncode}"
        ) from exc


def _normalize_symbol(raw: str) -> str | None:
    value = str(raw or "").strip().upper()
    if not value:
        return None
    bare = value.split(":")[-1]
    return bare or None


def load_watchlist_symbols(config: ScanConfig) -> tuple[str, ...]:
    if not config.tradingview_watchlists_path.exists():
        raise ValueError(
            f"TradingView watchlists file not found at {config.tradingview_watchlists_path}. "
            "Run `npm run tv:lists` first."
        )

    try:
        payload = json.loads(config.tradingview_watchlists_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"TradingView watchlists file is invalid JSON: {config.tradingview_watchlists_path}"
        ) from exc

    requested_names = {name.casefold(): name for name in config.watchlist_names}
    found_names: set[str] = set()
    symbols: list[str] = []
    holding_symbols: list[str] = []
    found_holding = False
    found_sections: set[str] = set()

    for watchlist in payload.get("watchlists", []):
        raw_name = str(watchlist.get("name", "")).strip()
        if raw_name.casefold() == config.holding_watchlist_name.casefold():
            found_holding = True
            sections = watchlist.get("sections") or {}
            requested_sections = {name.casefold(): name for name in config.holding_section_names}
            for section_name, raw_symbols in sections.items():
                if str(section_name).strip().casefold() not in requested_sections:
                    continue
                found_sections.add(str(section_name).strip().casefold())
                for raw_symbol in raw_symbols:
                    symbol = _normalize_symbol(raw_symbol)
                    if symbol:
                        holding_symbols.append(symbol)

        if raw_name.casefold() not in requested_names:
            continue
        found_names.add(raw_name.casefold())
        for raw_symbol in watchlist.get("symbols", []):
            symbol = _normalize_symbol(raw_symbol)
            if symbol:
                symbols.append(symbol)

    missing = [name for name in config.watchlist_names if name.casefold() not in found_names]
    if missing:
        available = [str(item.get("name", "")).strip() for item in payload.get("watchlists", [])]
        raise ValueError(
            "TradingView watchlists missing required lists "
            f"{missing} in {config.tradingview_watchlists_path}. Available: {available}"
        )

    if not found_holding:
        raise ValueError(
            f"TradingView watchlists missing holding watchlist '{config.holding_watchlist_name}' "
            f"in {config.tradingview_watchlists_path}"
        )

    missing_sections = [
        name for name in config.holding_section_names if name.casefold() not in found_sections
    ]
    if missing_sections:
        raise ValueError(
            f"Holding watchlist '{config.holding_watchlist_name}' is missing sections {missing_sections} "
            f"in {config.tradingview_watchlists_path}"
        )

    deduped = tuple(dict.fromkeys([*symbols, *holding_symbols]))
    if not deduped:
        raise ValueError(
            f"No symbols found in watchlists {config.watchlist_names} "
            f"from {config.tradingview_watchlists_path}"
        )
    return deduped


def resolve_scan_list(config: ScanConfig) -> tuple[str, ...]:
    refresh_tv_watchlists(config)

    if not config.tradingview_watchlists_refresh_command and config.tradingview_watchlists_path.exists():
        age_seconds = time.time() - config.tradingview_watchlists_path.stat().st_mtime
        if age_seconds > 7200:
            age_hours = age_seconds / 3600
            raise ValueError(
                f"TradingView watchlists file is {age_hours:.1f}h old "
                f"({config.tradingview_watchlists_path}). "
                "Set TRADINGVIEW_WATCHLISTS_REFRESH_COMMAND='npm run tv:lists' "
                "or run `npm run tv:lists` manually first."
            )

    return load_watchlist_symbols(config)


def pct_change(current: float, reference: float) -> float:
    return ((current - reference) / reference) * 100


def compute_scan_interval(
    bars: list[dict[str, Any]],
    previous_low: float,
    config: ScanConfig,
) -> int:
    regular_bars = [bar for bar in bars if _is_regular_bar(bar["t"])]
    if not regular_bars:
        return _INTERVAL_LOW

    ordered_bars = sorted(regular_bars, key=lambda bar: bar["t"])
    undercut_low: float | None = None
    last_price = ordered_bars[-1]["c"]

    for bar in ordered_bars:
        if undercut_low is None:
            if bar["l"] < previous_low:
                undercut_low = bar["l"]
            continue
        undercut_low = min(undercut_low, bar["l"])

    if undercut_low is None:
        distance_to_break = abs(pct_change(last_price, previous_low))
        return _INTERVAL_MEDIUM if distance_to_break <= 1.0 else _INTERVAL_LOW

    rebound_pct = pct_change(last_price, undercut_low)
    progress = rebound_pct / max(config.rebound_pct, 0.01)
    if progress >= 0.7:
        return _INTERVAL_URGENT
    if progress >= 0.3:
        return _INTERVAL_HIGH
    return _INTERVAL_MEDIUM


def evaluate_undercut_rally_scan(
    symbol: str,
    previous_low: float,
    previous_close: float,
    bars: list[dict[str, Any]],
    config: ScanConfig,
) -> ScanResult | None:
    regular_bars = [bar for bar in bars if _is_regular_bar(bar["t"])]
    if not regular_bars:
        return None

    ordered_bars = sorted(regular_bars, key=lambda bar: bar["t"])
    undercut_low: float | None = None
    undercut_time: datetime | None = None

    for bar in ordered_bars:
        if undercut_low is None:
            if bar["l"] >= previous_low:
                continue
            undercut_low = bar["l"]
            undercut_time = datetime.fromtimestamp(bar["t"] / 1000, tz=UTC).astimezone(EASTERN)
            continue

        if bar["l"] < undercut_low:
            undercut_low = bar["l"]
            undercut_time = datetime.fromtimestamp(bar["t"] / 1000, tz=UTC).astimezone(EASTERN)
            continue

        threshold = undercut_low * (1 + (config.rebound_pct / 100))
        trigger_price = bar["h"]
        if trigger_price < threshold:
            continue

        return ScanResult(
            symbol=symbol,
            previous_low=previous_low,
            previous_close=previous_close,
            undercut_low=undercut_low,
            undercut_time=undercut_time or datetime.fromtimestamp(bar["t"] / 1000, tz=UTC).astimezone(EASTERN),
            trigger_price=trigger_price,
            trigger_time=datetime.fromtimestamp(bar["t"] / 1000, tz=UTC).astimezone(EASTERN),
            rebound_from_low_pct=pct_change(trigger_price, undercut_low),
        )

    return None


def load_alert_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_alert_state(path: Path, state: dict[str, str]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def should_alert(result: ScanResult, state: dict[str, str], now: datetime) -> bool:
    key = f"U&R:{result.symbol}:{now.date().isoformat()}"
    raw_value = state.get(key)
    if raw_value is None:
        return True
    try:
        last_alerted_low = float(raw_value)
    except ValueError:
        return True
    return result.undercut_low < last_alerted_low


def mark_alert_sent(result: ScanResult, state: dict[str, str], now: datetime) -> None:
    key = f"U&R:{result.symbol}:{now.date().isoformat()}"
    state[key] = f"{result.undercut_low:.8f}"


def format_alert(result: ScanResult, now: datetime, config: ScanConfig) -> str:
    undercut_time_pt = result.undercut_time.astimezone(PACIFIC).strftime("%H:%M:%S")
    trigger_time_pt = result.trigger_time.astimezone(PACIFIC).strftime("%H:%M:%S")
    return (
        f"{result.symbol} U&R alert\n"
        f"Date: {now.strftime('%Y-%m-%d')}\n"
        f"Undercut time: {undercut_time_pt} PT\n"
        f"Trigger time: {trigger_time_pt} PT\n"
        f"Previous low: {result.previous_low:.2f}\n"
        f"Current low: {result.undercut_low:.2f}\n"
        f"Trigger price: {result.trigger_price:.2f}\n"
        f"Rebound from current low: +{result.rebound_from_low_pct:.2f}% "
        f"(rule: {config.rebound_pct:.2f}%+)\n"
        f"Previous close: {result.previous_close:.2f}"
    )


def send_alert(
    message: str,
    webhook_url: str | None,
    telegram_bot_token: str | None,
    telegram_chat_id: str | None,
) -> None:
    print(message, flush=True)

    if webhook_url:
        http_json_post(webhook_url, {"text": message})

    if telegram_bot_token and telegram_chat_id:
        http_json_post(
            f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage",
            {"chat_id": telegram_chat_id, "text": message},
        )


def scan_once_backtest(
    client: YFinanceClient,
    config: ScanConfig,
    now: datetime,
    symbols: tuple[str, ...],
) -> list[ScanResult]:
    matches: list[ScanResult] = []
    for symbol in symbols:
        try:
            previous_bar = client.get_previous_daily_bar(symbol, now)
            minute_bars = client.get_todays_minute_bars(symbol, now)
            result = evaluate_undercut_rally_scan(
                symbol=symbol,
                previous_low=previous_bar["l"],
                previous_close=previous_bar["c"],
                bars=minute_bars,
                config=config,
            )
            if result:
                matches.append(result)
        except Exception as exc:  # noqa: BLE001
            print(f"{symbol}: scan failed: {exc}", file=sys.stderr, flush=True)
    return matches


def prefetch_previous_bars(
    client: YFinanceClient,
    symbols: tuple[str, ...],
    now: datetime,
    limiter: RateLimiter,
) -> dict[str, SymbolState]:
    states: dict[str, SymbolState] = {}
    print(f"Pre-fetching previous daily bars for {len(symbols)} symbol(s)...", flush=True)
    for symbol in symbols:
        wait = limiter.seconds_until_available()
        if wait > 0:
            print(f"  Rate limit reached, waiting {wait:.1f}s...", flush=True)
            time.sleep(wait)
        try:
            bar = client.get_previous_daily_bar(symbol, now)
            limiter.consume()
            states[symbol] = SymbolState(
                previous_low=bar["l"],
                previous_close=bar["c"],
            )
            print(
                f"  {symbol}: prev_low={bar['l']:.2f} prev_close={bar['c']:.2f}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol}: skipped (prev bar fetch failed: {exc})", file=sys.stderr, flush=True)
    return states


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest-symbol")
    parser.add_argument("--backtest-date")
    args = parser.parse_args()

    config = load_config()
    client = YFinanceClient()

    if args.backtest_symbol and args.backtest_date:
        now = datetime.strptime(args.backtest_date, "%Y-%m-%d").replace(tzinfo=EASTERN)
        symbols = (args.backtest_symbol.upper(),)
        matches = scan_once_backtest(client, config, now, symbols)
        if not matches:
            print(f"No U&R trigger for {args.backtest_symbol.upper()} on {args.backtest_date}")
            return 0
        for result in matches:
            send_alert(
                format_alert(result, now, config),
                config.alert_webhook_url,
                config.telegram_bot_token,
                config.telegram_chat_id,
            )
        return 0

    symbols = resolve_scan_list(config)
    print(
        f"U&R scan list: {len(symbols)} symbol(s) from {', '.join(config.watchlist_names)}",
        flush=True,
    )
    print(f"Symbols: {', '.join(symbols)}", flush=True)
    print(f"Rate limit: {config.api_rate_limit} calls/min", flush=True)

    limiter = RateLimiter(config.api_rate_limit)
    alert_state = load_alert_state(config.alert_state_path)

    now = datetime.now(tz=EASTERN)
    symbol_states = prefetch_previous_bars(client, symbols, now, limiter)

    if not symbol_states:
        print("No symbols with valid previous daily bars. Exiting.", file=sys.stderr)
        return 1

    print(
        f"\nScanning {len(symbol_states)} symbol(s). Intervals: "
        f"LOW={_INTERVAL_LOW}s MEDIUM={_INTERVAL_MEDIUM}s "
        f"HIGH={_INTERVAL_HIGH}s URGENT={_INTERVAL_URGENT}s",
        flush=True,
    )

    while True:
        now = datetime.now(tz=EASTERN)

        if not _is_market_hours(now):
            print(
                f"Outside market hours ({now.strftime('%H:%M:%S %Z')}), waiting...",
                flush=True,
            )
            time.sleep(config.poll_seconds)
            continue

        current_ts = time.time()
        due = sorted(
            [sym for sym, st in symbol_states.items() if current_ts >= st.next_scan_at],
            key=lambda s: (symbol_states[s].interval, symbol_states[s].next_scan_at),
        )

        available = limiter.available()
        to_scan = due[:available]

        if to_scan:
            print(
                f"{now.strftime('%H:%M:%S')} scanning [{', '.join(to_scan)}] "
                f"({available} calls avail, {len(due) - len(to_scan)} deferred)",
                flush=True,
            )

            for symbol in to_scan:
                state = symbol_states[symbol]
                try:
                    minute_bars = client.get_todays_minute_bars(symbol, now)
                    limiter.consume()

                    state.interval = compute_scan_interval(
                        minute_bars,
                        state.previous_low,
                        config,
                    )
                    state.next_scan_at = time.time() + state.interval

                    result = evaluate_undercut_rally_scan(
                        symbol=symbol,
                        previous_low=state.previous_low,
                        previous_close=state.previous_close,
                        bars=minute_bars,
                        config=config,
                    )
                    if result and should_alert(result, alert_state, now):
                        send_alert(
                            format_alert(result, now, config),
                            config.alert_webhook_url,
                            config.telegram_bot_token,
                            config.telegram_chat_id,
                        )
                        mark_alert_sent(result, alert_state, now)
                        save_alert_state(config.alert_state_path, alert_state)
                except Exception as exc:  # noqa: BLE001
                    print(f"{symbol}: scan failed: {exc}", file=sys.stderr, flush=True)
                    state.next_scan_at = time.time() + 60

        wait = limiter.seconds_until_available()
        time.sleep(wait if wait > 0 and due else 1.0)


if __name__ == "__main__":
    raise SystemExit(run())
