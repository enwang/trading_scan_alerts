#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")
ENV_PATH = Path(".env")


@dataclass(frozen=True)
class ScanConfig:
    polygon_api_key: str
    reversal_scan_list: tuple[str, ...]
    tradingview_screens_path: Path = Path("tv-output/all-screens.json")
    tradingview_screens_refresh_command: str | None = None
    postmarket_screener_name: str = "Post market gap down"
    premarket_screener_name: str = "Pre market gap down"
    scan_list_path: Path = Path("tv-output/scan-list.json")
    premarket_drawdown_pct: float = 6.0
    regular_session_rebound_pct: float = 4.0
    distance_to_reference_pct: float = 1.5
    min_regular_session_gain_pct: float = 3.0
    poll_seconds: int = 60
    alert_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    alert_state_path: Path = Path("alert_state.json")


@dataclass(frozen=True)
class ScanResult:
    symbol: str
    previous_close: float
    previous_high: float
    premarket_low: float
    regular_open: float
    trigger_price: float
    trigger_time: datetime
    session_high_at_trigger: float
    premarket_drawdown_pct: float
    rebound_from_premarket_low_pct: float
    regular_session_gain_pct: float
    distance_to_previous_close_pct: float
    distance_to_previous_high_pct: float
    near_previous_close: bool
    near_previous_high: bool


class PolygonClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._grouped_daily_cache: dict[str, list[dict[str, Any]]] = {}

    def get_previous_daily_bar(self, symbol: str, today: datetime) -> dict[str, Any]:
        start = (today - timedelta(days=10)).date().isoformat()
        end = (today - timedelta(days=1)).date().isoformat()
        url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
        data = self._get(url, {"adjusted": "true", "sort": "desc", "limit": 10})
        results = data.get("results", [])
        if not results:
            raise ValueError(f"No previous daily bar found for {symbol}")
        latest = sorted(results, key=lambda row: row["t"], reverse=True)[0]
        return latest

    def get_todays_minute_bars(self, symbol: str, today: datetime) -> list[dict[str, Any]]:
        day = today.date().isoformat()
        url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/minute/{day}/{day}"
        data = self._get(url, {"adjusted": "true", "sort": "asc", "limit": 50000})
        return data.get("results", [])

    def get_grouped_daily_bars(self, day: datetime) -> list[dict[str, Any]]:
        day_str = day.date().isoformat()
        if day_str in self._grouped_daily_cache:
            return self._grouped_daily_cache[day_str]
        data = self._get(
            f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{day_str}",
            {"adjusted": "true", "include_otc": "false"},
        )
        results = data.get("results", [])
        self._grouped_daily_cache[day_str] = results
        return results

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = http_json_get(url, {**params, "apiKey": self.api_key})
        if payload.get("status") == "ERROR":
            raise ValueError(payload.get("error", "Polygon request failed"))
        return payload


def http_json_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    full_url = f"{url}?{urlencode(params)}"
    request = Request(full_url, headers={"User-Agent": "trading-scan/1.0"})
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise ValueError(f"Network error for {url}: {exc.reason}") from exc


def http_json_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "User-Agent": "trading-scan/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raise ValueError(f"HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise ValueError(f"Network error for {url}: {exc.reason}") from exc


def load_config() -> ScanConfig:
    load_dotenv(ENV_PATH)

    api_key = os.getenv("POLYGON_API_KEY", "").strip()
    if not api_key:
        raise ValueError("POLYGON_API_KEY is required")

    reversal_list_raw = os.getenv("REVERSAL_SCAN_LIST", "").strip()
    reversal_scan_list = tuple(
        symbol.strip().upper()
        for symbol in reversal_list_raw.split(",")
        if symbol.strip()
    )

    return ScanConfig(
        polygon_api_key=api_key,
        reversal_scan_list=reversal_scan_list,
        tradingview_screens_path=Path(os.getenv("TRADINGVIEW_SCREENS_PATH", "tv-output/all-screens.json")),
        tradingview_screens_refresh_command=os.getenv("TRADINGVIEW_SCREENS_REFRESH_COMMAND", "").strip() or None,
        postmarket_screener_name=os.getenv("POSTMARKET_SCREENER_NAME", "Post market gap down"),
        premarket_screener_name=os.getenv("PREMARKET_SCREENER_NAME", "Pre market gap down"),
        scan_list_path=Path(os.getenv("SCAN_LIST_PATH", "tv-output/scan-list.json")),
        premarket_drawdown_pct=float(os.getenv("PREMARKET_DRAWDOWN_PCT", "6")),
        regular_session_rebound_pct=float(os.getenv("REGULAR_SESSION_REBOUND_PCT", "4")),
        distance_to_reference_pct=float(os.getenv("DISTANCE_TO_REFERENCE_PCT", "1.5")),
        min_regular_session_gain_pct=float(os.getenv("MIN_REGULAR_SESSION_GAIN_PCT", "3")),
        poll_seconds=int(os.getenv("POLL_SECONDS", "60")),
        alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL", "").strip() or None,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip() or None,
        alert_state_path=Path(os.getenv("ALERT_STATE_PATH", "alert_state.json")),
    )


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


# ---------------------------------------------------------------------------
# TradingView screener helpers
# ---------------------------------------------------------------------------


def refresh_tv_screens(config: ScanConfig) -> None:
    if not config.tradingview_screens_refresh_command:
        print("No TRADINGVIEW_SCREENS_REFRESH_COMMAND set; skipping screen refresh.", flush=True)
        return
    try:
        subprocess.run(
            shlex.split(config.tradingview_screens_refresh_command),
            check=True,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            f"TradingView screens refresh command not found: {config.tradingview_screens_refresh_command}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"TradingView screens refresh failed with exit code {exc.returncode}"
        ) from exc


def load_screener_symbols(screener_name: str, screens_path: Path) -> tuple[str, ...]:
    if not screens_path.exists():
        raise ValueError(
            f"TradingView screens file not found at {screens_path}. "
            "Run `npm run tv:screens` first."
        )
    try:
        payload = json.loads(screens_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"TradingView screens file is invalid JSON: {screens_path}") from exc

    screeners = payload.get("screeners", [])
    for screener in screeners:
        if screener.get("name") == screener_name:
            rows = screener.get("rows", [])
            symbols: list[str] = []
            for row in rows:
                sym = row.get("symbol", "")
                if sym:
                    bare = sym.split(":")[-1] if ":" in sym else sym
                    if bare:
                        symbols.append(bare.upper())
            return tuple(dict.fromkeys(symbols))

    available = [s.get("name") for s in screeners]
    raise ValueError(
        f"Screener '{screener_name}' not found in {screens_path}. "
        f"Available screeners: {available}"
    )


# ---------------------------------------------------------------------------
# Scan list persistence (two-phase merged list)
# ---------------------------------------------------------------------------


def load_scan_list(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_scan_list(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def get_scan_list_symbols(path: Path) -> tuple[str, ...]:
    data = load_scan_list(path)
    postmarket_symbols: list[str] = data.get("postmarket", {}).get("symbols", [])
    premarket_symbols: list[str] = data.get("premarket", {}).get("symbols", [])
    return tuple(dict.fromkeys([*postmarket_symbols, *premarket_symbols]))


def build_list_phase(phase: str, config: ScanConfig) -> None:
    """Build (or merge into) the scan list from a TradingView screener.

    phase="postmarket"  → runs at ~5PM after close; resets list with post-market gap-down results.
    phase="premarket"   → runs at ~5:30AM; merges pre-market gap-down results into the list.
    """
    print(f"[{phase}] Refreshing TradingView screens...", flush=True)
    refresh_tv_screens(config)

    screener_name = (
        config.postmarket_screener_name
        if phase == "postmarket"
        else config.premarket_screener_name
    )
    print(f"[{phase}] Loading screener '{screener_name}'...", flush=True)
    symbols = load_screener_symbols(screener_name, config.tradingview_screens_path)
    print(f"[{phase}] Found {len(symbols)} symbols: {', '.join(symbols)}", flush=True)

    now_str = datetime.now(tz=EASTERN).isoformat()

    if phase == "postmarket":
        # Start fresh for the new trading day; clear any stale pre-market data.
        data: dict[str, Any] = {
            "postmarket": {"symbols": list(symbols), "built_at": now_str},
            "premarket": {},
        }
    else:
        data = load_scan_list(config.scan_list_path)
        data["premarket"] = {"symbols": list(symbols), "built_at": now_str}
        if "postmarket" not in data:
            data["postmarket"] = {}

    save_scan_list(config.scan_list_path, data)

    total = get_scan_list_symbols(config.scan_list_path)
    print(
        f"[{phase}] Scan list saved → {config.scan_list_path} "
        f"({len(total)} total symbols after merge)",
        flush=True,
    )


def resolve_reversal_scan_list(config: ScanConfig) -> tuple[str, ...]:
    """Return the symbols to scan: scan-list file + any manual overrides from env."""
    scan_list_symbols = list(get_scan_list_symbols(config.scan_list_path))
    manual_symbols = list(config.reversal_scan_list)
    merged = tuple(dict.fromkeys([*scan_list_symbols, *manual_symbols]))
    if not merged:
        raise ValueError(
            "Scan list is empty. Build it first:\n"
            "  After market close (~5PM):    python scan_reversal_alert.py --build-list postmarket\n"
            "  Before market open (~5:30AM): python scan_reversal_alert.py --build-list premarket"
        )
    return merged


# ---------------------------------------------------------------------------
# Scan logic
# ---------------------------------------------------------------------------


def evaluate_reversal_scan(
    symbol: str,
    previous_close: float,
    previous_high: float,
    bars: list[dict[str, Any]],
    config: ScanConfig,
) -> ScanResult | None:
    if not bars:
        return None

    premarket_bars = [bar for bar in bars if _is_premarket_bar(bar["t"])]
    regular_bars = [bar for bar in bars if _is_regular_bar(bar["t"])]
    if not premarket_bars or not regular_bars:
        return None

    premarket_low = min(bar["l"] for bar in premarket_bars)
    regular_open = regular_bars[0]["o"]

    premarket_drawdown_pct = pct_change(premarket_low, previous_close)

    if premarket_drawdown_pct > -config.premarket_drawdown_pct:
        return None

    session_high_so_far = regular_open
    for bar in regular_bars:
        trigger_price = bar["c"]
        session_high_so_far = max(session_high_so_far, bar["h"])
        rebound_from_premarket_low_pct = pct_change(trigger_price, premarket_low)
        regular_session_gain_pct = pct_change(trigger_price, regular_open)
        distance_to_previous_close_pct = abs((trigger_price - previous_close) / previous_close) * 100
        distance_to_previous_high_pct = abs((trigger_price - previous_high) / previous_high) * 100
        near_previous_close = distance_to_previous_close_pct <= config.distance_to_reference_pct
        near_previous_high = distance_to_previous_high_pct <= config.distance_to_reference_pct

        if rebound_from_premarket_low_pct < config.regular_session_rebound_pct:
            continue
        if regular_session_gain_pct < config.min_regular_session_gain_pct:
            continue
        if trigger_price < regular_open:
            continue
        if trigger_price < session_high_so_far * 0.985:
            continue
        if not (near_previous_close or near_previous_high):
            continue

        return ScanResult(
            symbol=symbol,
            previous_close=previous_close,
            previous_high=previous_high,
            premarket_low=premarket_low,
            regular_open=regular_open,
            trigger_price=trigger_price,
            trigger_time=datetime.fromtimestamp(bar["t"] / 1000, tz=UTC).astimezone(EASTERN),
            session_high_at_trigger=session_high_so_far,
            premarket_drawdown_pct=premarket_drawdown_pct,
            rebound_from_premarket_low_pct=rebound_from_premarket_low_pct,
            regular_session_gain_pct=regular_session_gain_pct,
            distance_to_previous_close_pct=distance_to_previous_close_pct,
            distance_to_previous_high_pct=distance_to_previous_high_pct,
            near_previous_close=near_previous_close,
            near_previous_high=near_previous_high,
        )

    return None


def pct_change(current: float, reference: float) -> float:
    return ((current - reference) / reference) * 100


def _is_premarket_bar(timestamp_ms: int) -> bool:
    candle_time = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).astimezone(EASTERN)
    return candle_time.hour >= 4 and candle_time < candle_time.replace(hour=9, minute=30, second=0, microsecond=0)


def _is_regular_bar(timestamp_ms: int) -> bool:
    candle_time = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).astimezone(EASTERN)
    start = candle_time.replace(hour=9, minute=30, second=0, microsecond=0)
    end = candle_time.replace(hour=16, minute=0, second=0, microsecond=0)
    return start <= candle_time < end


def _is_market_hours(now: datetime) -> bool:
    local_now = now.astimezone(EASTERN)
    start = local_now.replace(hour=9, minute=30, second=0, microsecond=0)
    end = local_now.replace(hour=16, minute=0, second=0, microsecond=0)
    return start <= local_now < end


def previous_business_day(day: datetime) -> datetime:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


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
    key = f"{result.symbol}:{now.date().isoformat()}"
    return state.get(key) != "sent"


def mark_alert_sent(result: ScanResult, state: dict[str, str], now: datetime) -> None:
    key = f"{result.symbol}:{now.date().isoformat()}"
    state[key] = "sent"


def format_alert(result: ScanResult, now: datetime) -> str:
    references = []
    if result.near_previous_close:
        references.append(f"Prev close {result.previous_close:.2f}")
    if result.near_previous_high:
        references.append(f"Prev high {result.previous_high:.2f}")
    refs = " | ".join(references)
    return (
        f"{result.symbol} intraday reversal alert\n"
        f"Date: {now.strftime('%Y-%m-%d')}\n"
        f"Trigger time: {result.trigger_time.strftime('%H:%M:%S %Z')}\n"
        f"Trigger price: {result.trigger_price:.2f}\n"
        f"Premarket low: {result.premarket_low:.2f} ({result.premarket_drawdown_pct:.2f}% vs prev close)\n"
        f"Regular open: {result.regular_open:.2f}\n"
        f"Rebound from PM low: +{result.rebound_from_premarket_low_pct:.2f}%\n"
        f"Gain from open: +{result.regular_session_gain_pct:.2f}%\n"
        f"Reference zone: {refs}"
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


def scan_once(
    client: PolygonClient,
    config: ScanConfig,
    now: datetime,
    symbols: tuple[str, ...],
) -> list[ScanResult]:
    matches: list[ScanResult] = []
    for symbol in symbols:
        try:
            previous_bar = client.get_previous_daily_bar(symbol, now)
            minute_bars = client.get_todays_minute_bars(symbol, now)
            result = evaluate_reversal_scan(
                symbol=symbol,
                previous_close=previous_bar["c"],
                previous_high=previous_bar["h"],
                bars=minute_bars,
                config=config,
            )
            if result:
                matches.append(result)
        except Exception as exc:  # noqa: BLE001
            print(f"{symbol}: scan failed: {exc}", file=sys.stderr, flush=True)
    return matches


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--build-list",
        choices=["postmarket", "premarket"],
        help=(
            "Build the scan list from a TradingView screener and exit. "
            "Use 'postmarket' at ~5PM after market close, "
            "'premarket' at ~5:30AM before market open."
        ),
    )
    parser.add_argument("--backtest-symbol")
    parser.add_argument("--backtest-date")
    args = parser.parse_args()

    config = load_config()
    client = PolygonClient(config.polygon_api_key)

    # ------------------------------------------------------------------
    # List-building modes (run via cron, then exit)
    # ------------------------------------------------------------------
    if args.build_list:
        build_list_phase(args.build_list, config)
        return 0

    # ------------------------------------------------------------------
    # Backtest mode
    # ------------------------------------------------------------------
    if args.backtest_symbol and args.backtest_date:
        now = datetime.strptime(args.backtest_date, "%Y-%m-%d").replace(tzinfo=EASTERN)
        symbols = (args.backtest_symbol.upper(),)
        matches = scan_once(
            client,
            ScanConfig(**{**config.__dict__, "reversal_scan_list": symbols}),
            now,
            symbols,
        )
        if not matches:
            print(f"No trigger for {args.backtest_symbol.upper()} on {args.backtest_date}")
            return 0
        for result in matches:
            send_alert(
                format_alert(result, now),
                config.alert_webhook_url,
                config.telegram_bot_token,
                config.telegram_chat_id,
            )
        return 0

    # ------------------------------------------------------------------
    # Live scan loop — only fires during regular market hours (9:30–4PM ET)
    # ------------------------------------------------------------------
    symbols = resolve_reversal_scan_list(config)
    data = load_scan_list(config.scan_list_path)
    postmarket_built = data.get("postmarket", {}).get("built_at", "n/a")
    premarket_built = data.get("premarket", {}).get("built_at", "n/a")
    print(
        f"Scan list loaded: {len(symbols)} symbols "
        f"(postmarket built: {postmarket_built}, premarket built: {premarket_built})",
        flush=True,
    )
    print(f"Symbols: {', '.join(symbols)}", flush=True)

    state = load_alert_state(config.alert_state_path)

    while True:
        now = datetime.now(tz=EASTERN)

        if _is_market_hours(now):
            matches = scan_once(client, config, now, symbols)
            for result in matches:
                if should_alert(result, state, now):
                    send_alert(
                        format_alert(result, now),
                        config.alert_webhook_url,
                        config.telegram_bot_token,
                        config.telegram_chat_id,
                    )
                    mark_alert_sent(result, state, now)
                    save_alert_state(config.alert_state_path, state)
        else:
            print(
                f"Outside market hours ({now.strftime('%H:%M:%S %Z')}), waiting...",
                flush=True,
            )

        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(run())
