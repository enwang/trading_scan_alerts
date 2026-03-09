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
    tradingview_watchlist_enabled: bool = False
    tradingview_watchlist_path: Path = Path("tv-output/watchlist.json")
    tradingview_watchlist_refresh_command: str | None = None
    tradingview_watchlist_refresh_seconds: int = 900
    premarket_drawdown_pct: float = 6.0
    regular_session_rebound_pct: float = 4.0
    distance_to_reference_pct: float = 1.5
    min_regular_session_gain_pct: float = 3.0
    prefilter_enabled: bool = True
    prefilter_drawdown_pct: float = 8.0
    prefilter_min_price: float = 5.0
    prefilter_min_volume: int = 500_000
    prefilter_max_symbols: int = 150
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

    def get_full_market_snapshot(self, include_otc: bool = False) -> list[dict[str, Any]]:
        data = self._get(
            "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
            {"include_otc": str(include_otc).lower()},
        )
        return data.get("tickers", [])

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
        tradingview_watchlist_enabled=os.getenv("TRADINGVIEW_WATCHLIST_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        tradingview_watchlist_path=Path(os.getenv("TRADINGVIEW_WATCHLIST_PATH", "tv-output/watchlist.json")),
        tradingview_watchlist_refresh_command=os.getenv("TRADINGVIEW_WATCHLIST_REFRESH_COMMAND", "").strip() or None,
        tradingview_watchlist_refresh_seconds=int(os.getenv("TRADINGVIEW_WATCHLIST_REFRESH_SECONDS", "900")),
        premarket_drawdown_pct=float(os.getenv("PREMARKET_DRAWDOWN_PCT", "6")),
        regular_session_rebound_pct=float(os.getenv("REGULAR_SESSION_REBOUND_PCT", "4")),
        distance_to_reference_pct=float(os.getenv("DISTANCE_TO_REFERENCE_PCT", "1.5")),
        min_regular_session_gain_pct=float(os.getenv("MIN_REGULAR_SESSION_GAIN_PCT", "3")),
        prefilter_enabled=os.getenv("REVERSAL_PREFILTER_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
        prefilter_drawdown_pct=float(os.getenv("REVERSAL_PREFILTER_DRAWDOWN_PCT", "8")),
        prefilter_min_price=float(os.getenv("REVERSAL_PREFILTER_MIN_PRICE", "5")),
        prefilter_min_volume=int(os.getenv("REVERSAL_PREFILTER_MIN_VOLUME", "500000")),
        prefilter_max_symbols=int(os.getenv("REVERSAL_PREFILTER_MAX_SYMBOLS", "150")),
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


def evaluate_reversal_scan(
    symbol: str,
    previous_close: float,
    previous_high: float,
    bars: list[dict[str, Any]],
    now: datetime,
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


def _is_premarket(now: datetime) -> bool:
    local_now = now.astimezone(EASTERN)
    start = local_now.replace(hour=4, minute=0, second=0, microsecond=0)
    open_time = local_now.replace(hour=9, minute=30, second=0, microsecond=0)
    return start <= local_now < open_time


def _is_postmarket(now: datetime) -> bool:
    local_now = now.astimezone(EASTERN)
    close_time = local_now.replace(hour=16, minute=0, second=0, microsecond=0)
    end = local_now.replace(hour=20, minute=0, second=0, microsecond=0)
    return close_time <= local_now < end


def previous_business_day(day: datetime) -> datetime:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def previous_trading_date(client: PolygonClient, day: datetime, max_lookback_days: int = 10) -> datetime:
    candidate = previous_business_day(day)
    checked = 0
    while checked < max_lookback_days:
        try:
            if client.get_grouped_daily_bars(candidate):
                return candidate
        except ValueError as exc:
            if "HTTP 403" not in str(exc):
                raise
        candidate = previous_business_day(candidate)
        checked += 1
    raise ValueError("Could not resolve a previous trading day from grouped daily data")


def latest_grouped_date_on_or_before(
    client: PolygonClient,
    day: datetime,
    max_lookback_days: int = 10,
) -> tuple[datetime, list[dict[str, Any]]]:
    candidate = day
    checked = 0
    while checked <= max_lookback_days:
        try:
            bars = client.get_grouped_daily_bars(candidate)
            if bars:
                return candidate, bars
        except ValueError as exc:
            if "HTTP 403" not in str(exc):
                raise
        candidate = previous_business_day(candidate)
        checked += 1
    raise ValueError("Could not resolve a grouped daily data date")


def build_prefilter_symbol_list(client: PolygonClient, config: ScanConfig, now: datetime) -> tuple[str, ...]:
    return tuple(item["symbol"] for item in build_prefilter_candidates(client, config, now))


def maybe_refresh_tradingview_watchlist(config: ScanConfig) -> None:
    if not config.tradingview_watchlist_enabled or not config.tradingview_watchlist_refresh_command:
        return

    watchlist_path = config.tradingview_watchlist_path
    if watchlist_path.exists():
        age_seconds = time.time() - watchlist_path.stat().st_mtime
        if age_seconds < config.tradingview_watchlist_refresh_seconds:
            return

    try:
        subprocess.run(
            shlex.split(config.tradingview_watchlist_refresh_command),
            check=True,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            f"TradingView watchlist refresh command not found: {config.tradingview_watchlist_refresh_command}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"TradingView watchlist refresh failed with exit code {exc.returncode}"
        ) from exc


def load_tradingview_watchlist_symbols(config: ScanConfig) -> tuple[str, ...]:
    if not config.tradingview_watchlist_enabled:
        return ()

    maybe_refresh_tradingview_watchlist(config)

    path = config.tradingview_watchlist_path
    if not path.exists():
        raise ValueError(
            f"TradingView watchlist file not found at {path}. "
            "Bootstrap the saved session first with `npm run tv:login`, then run `npm run tv:watchlist`."
        )

    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"TradingView watchlist file is invalid JSON: {path}") from exc

    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError(f"TradingView watchlist payload is missing a symbols list: {path}")

    normalized = [
        symbol.strip().upper()
        for symbol in symbols
        if isinstance(symbol, str) and symbol.strip()
    ]
    return tuple(dict.fromkeys(normalized))


def build_prefilter_candidates(
    client: PolygonClient,
    config: ScanConfig,
    now: datetime,
) -> list[dict[str, Any]]:
    try:
        return build_snapshot_prefilter_candidates(client, config, now)
    except ValueError as exc:
        if "HTTP 403" not in str(exc):
            raise
        if _is_premarket(now) or _is_postmarket(now):
            raise ValueError(
                "Full-market pre/post-market prefilter requires Polygon snapshot access. "
                "Your key cannot access that endpoint, so grouped daily fallback would be stale."
            ) from exc
        return build_grouped_prefilter_candidates(client, config, now)


def build_snapshot_prefilter_candidates(
    client: PolygonClient,
    config: ScanConfig,
    now: datetime,
) -> list[dict[str, Any]]:
    snapshots = client.get_full_market_snapshot(include_otc=False)
    candidates: list[dict[str, Any]] = []

    for snapshot in snapshots:
        symbol = snapshot.get("ticker")
        prev_day = snapshot.get("prevDay") or {}
        day = snapshot.get("day") or {}
        min_bar = snapshot.get("min") or {}
        last_trade = snapshot.get("lastTrade") or {}
        prev_close = prev_day.get("c")
        if not symbol or not prev_close:
            continue

        last_price = last_trade.get("p") or min_bar.get("c") or day.get("c")
        if not last_price or last_price < config.prefilter_min_price:
            continue

        volume = day.get("v") or min_bar.get("v") or 0
        if volume < config.prefilter_min_volume:
            continue

        if _is_premarket(now) or _is_postmarket(now):
            drawdown_pct = pct_change(last_price, prev_close)
        else:
            day_low = day.get("l")
            if not day_low:
                continue
            drawdown_pct = pct_change(day_low, prev_close)

        if drawdown_pct > -config.prefilter_drawdown_pct:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "drawdown_pct": drawdown_pct,
                "volume": volume,
                "last_price": last_price,
                "previous_close": prev_close,
                "source": "snapshot",
            }
        )

    candidates.sort(key=lambda item: (item["drawdown_pct"], -item["volume"]))
    return candidates[: config.prefilter_max_symbols]


def build_grouped_prefilter_candidates(
    client: PolygonClient,
    config: ScanConfig,
    now: datetime,
) -> list[dict[str, Any]]:
    grouped_date, grouped_bars = latest_grouped_date_on_or_before(client, now)
    previous_date = previous_trading_date(client, grouped_date)
    previous_bars = client.get_grouped_daily_bars(previous_date)
    previous_by_symbol = {bar.get("T"): bar for bar in previous_bars if bar.get("T")}
    candidates: list[dict[str, Any]] = []

    for bar in grouped_bars:
        symbol = bar.get("T")
        previous_bar = previous_by_symbol.get(symbol)
        if not symbol or not previous_bar:
            continue

        previous_close = previous_bar.get("c")
        last_price = bar.get("c")
        day_low = bar.get("l")
        volume = bar.get("v") or 0
        if not previous_close or not last_price or not day_low:
            continue
        if last_price < config.prefilter_min_price or volume < config.prefilter_min_volume:
            continue

        drawdown_pct = pct_change(day_low, previous_close)
        if drawdown_pct > -config.prefilter_drawdown_pct:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "drawdown_pct": drawdown_pct,
                "volume": volume,
                "last_price": last_price,
                "previous_close": previous_close,
                "source": f"grouped:{grouped_date.date().isoformat()}",
            }
        )

    candidates.sort(key=lambda item: (item["drawdown_pct"], -item["volume"]))
    return candidates[: config.prefilter_max_symbols]


def resolve_reversal_scan_list(client: PolygonClient, config: ScanConfig, now: datetime) -> tuple[str, ...]:
    manual_symbols = list(config.reversal_scan_list)
    tradingview_symbols = list(load_tradingview_watchlist_symbols(config))
    if not config.prefilter_enabled:
        merged = tuple(dict.fromkeys([*manual_symbols, *tradingview_symbols]))
        if not merged:
            raise ValueError(
                "REVERSAL_SCAN_LIST and TradingView watchlist are empty while REVERSAL_PREFILTER_ENABLED is false"
            )
        return merged

    prefilter_symbols = build_prefilter_symbol_list(client, config, now)
    merged = tuple(dict.fromkeys([*manual_symbols, *tradingview_symbols, *prefilter_symbols]))
    if not merged:
        raise ValueError(
            "No reversal scan candidates found from REVERSAL_SCAN_LIST, TradingView watchlist, or the prefilter"
        )
    return merged


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


def scan_once(client: PolygonClient, config: ScanConfig, now: datetime) -> list[ScanResult]:
    matches: list[ScanResult] = []
    symbols = resolve_reversal_scan_list(client, config, now)
    for symbol in symbols:
        try:
            previous_bar = client.get_previous_daily_bar(symbol, now)
            minute_bars = client.get_todays_minute_bars(symbol, now)
            result = evaluate_reversal_scan(
                symbol=symbol,
                previous_close=previous_bar["c"],
                previous_high=previous_bar["h"],
                bars=minute_bars,
                now=now,
                config=config,
            )
            if result:
                matches.append(result)
        except Exception as exc:  # noqa: BLE001
            print(f"{symbol}: scan failed: {exc}", file=sys.stderr, flush=True)
    return matches


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest-symbol")
    parser.add_argument("--backtest-date")
    parser.add_argument("--show-prefilter", action="store_true")
    args = parser.parse_args()

    config = load_config()
    client = PolygonClient(config.polygon_api_key)

    if args.show_prefilter:
        now = datetime.now(tz=EASTERN)
        candidates = build_prefilter_candidates(client, config, now)
        if not candidates:
            print("No prefilter candidates found.")
            return 0
        for item in candidates:
            print(
                f"{item['symbol']}: drawdown={item['drawdown_pct']:.2f}% "
                f"last={item['last_price']:.2f} prev_close={item['previous_close']:.2f} "
                f"volume={int(item['volume'])} source={item['source']}"
            )
        return 0

    if args.backtest_symbol and args.backtest_date:
        now = datetime.strptime(args.backtest_date, "%Y-%m-%d").replace(tzinfo=EASTERN)
        matches = scan_once(
            client,
            ScanConfig(**{**config.__dict__, "reversal_scan_list": (args.backtest_symbol.upper(),), "prefilter_enabled": False}),
            now,
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

    state = load_alert_state(config.alert_state_path)

    while True:
        now = datetime.now(tz=EASTERN)
        matches = scan_once(client, config, now)
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
        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(run())
