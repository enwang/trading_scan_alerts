from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def fetch_telegram_updates(bot_token: str, offset: int | None = None) -> list[dict[str, Any]]:
    query = {"timeout": "0"}
    if offset is not None:
        query["offset"] = str(offset)
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates?{urlencode(query)}"
    request = Request(url, headers={"User-Agent": "trading-scan/1.0"}, method="GET")
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"HTTP {exc.code} for Telegram getUpdates") from exc
    except URLError as exc:
        raise ValueError(f"Network error for Telegram getUpdates: {exc.reason}") from exc

    if not payload.get("ok"):
        raise ValueError(f"Telegram getUpdates failed: {payload}")
    results = payload.get("result", [])
    return results if isinstance(results, list) else []


def _muted_symbols_key(alert_type: str, now: datetime) -> str:
    return f"{alert_type}:MUTED:{now.date().isoformat()}"


def _muted_alert_type_key(alert_type: str, now: datetime) -> str:
    return f"{alert_type}:MUTED_TYPE:{now.date().isoformat()}"


def load_muted_symbols(state: dict[str, Any], alert_type: str, now: datetime) -> set[str]:
    raw_value = state.get(_muted_symbols_key(alert_type, now), [])
    if not isinstance(raw_value, list):
        return set()
    return {str(symbol).strip().upper() for symbol in raw_value if str(symbol).strip()}


def save_muted_symbols(
    state: dict[str, Any],
    alert_type: str,
    now: datetime,
    symbols: set[str],
) -> None:
    state[_muted_symbols_key(alert_type, now)] = sorted(symbols)


def is_alert_type_muted(state: dict[str, Any], alert_type: str, now: datetime) -> bool:
    return bool(state.get(_muted_alert_type_key(alert_type, now)))


def set_alert_type_muted(
    state: dict[str, Any],
    alert_type: str,
    now: datetime,
    muted: bool,
) -> None:
    key = _muted_alert_type_key(alert_type, now)
    if muted:
        state[key] = True
    else:
        state.pop(key, None)


def parse_telegram_control_command(
    text: str,
    *,
    alert_aliases: set[str],
) -> tuple[str, str, str | None] | None:
    tokens = text.strip().split()
    if not tokens:
        return None

    action = tokens[0].casefold()
    if action not in {"stop", "resume"}:
        return None

    if len(tokens) == 2 and tokens[1].casefold() in {"--help", "help"}:
        return action, "help", None

    if len(tokens) == 2 and tokens[1].casefold() in alert_aliases:
        return action, "type", None

    if len(tokens) == 2:
        symbol = str(tokens[1]).strip().upper()
        return (action, "symbol", symbol) if symbol else None

    if len(tokens) == 3 and tokens[1].casefold() in alert_aliases:
        symbol = str(tokens[2]).strip().upper()
        return (action, "symbol", symbol) if symbol else None

    return None


def build_help_message(alert_label: str, alias_examples: tuple[str, ...]) -> str:
    base_alias = alias_examples[0]
    return "\n".join([
        f"{alert_label} Telegram controls",
        "Commands:",
        "stop --help",
        f"stop {base_alias}",
        f"resume {base_alias}",
        "stop AAPL",
        "resume AAPL",
        f"stop {base_alias} AAPL",
        f"resume {base_alias} AAPL",
        f"`stop {base_alias}` mutes the whole {alert_label} type for today.",
        "`stop AAPL` mutes only that ticker for today.",
    ])


def process_telegram_commands(
    *,
    bot_token: str | None,
    chat_id: str | None,
    state: dict[str, Any],
    now: datetime,
    alert_type: str,
    alert_label: str,
    alert_aliases: set[str],
    help_alias_examples: tuple[str, ...],
    send_confirmation: Callable[[str], None],
) -> bool:
    if not bot_token or not chat_id:
        return False

    raw_offset = state.get("telegram_update_offset")
    offset = int(raw_offset) if isinstance(raw_offset, int) else None
    updates = fetch_telegram_updates(bot_token, offset)
    if not updates:
        return False

    changed = False
    muted_symbols = load_muted_symbols(state, alert_type, now)
    allowed_chat_id = str(chat_id)

    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            state["telegram_update_offset"] = update_id + 1
            changed = True

        message = update.get("message")
        if not isinstance(message, dict):
            continue

        chat = message.get("chat")
        current_chat_id = None
        if isinstance(chat, dict):
            raw_chat_id = chat.get("id")
            if raw_chat_id is not None:
                current_chat_id = str(raw_chat_id)
        if current_chat_id != allowed_chat_id:
            continue

        command = parse_telegram_control_command(
            str(message.get("text", "")).strip(),
            alert_aliases=alert_aliases,
        )
        if command is None:
            continue

        action, target_kind, target_value = command
        if target_kind == "help":
            send_confirmation(build_help_message(alert_label, help_alias_examples))
            continue

        if target_kind == "type":
            muted = action == "stop"
            previous = is_alert_type_muted(state, alert_type, now)
            set_alert_type_muted(state, alert_type, now, muted)
            if previous != muted:
                changed = True
            verb = "Muted" if muted else "Resumed"
            send_confirmation(f"{verb} all {alert_label} alerts on {now.strftime('%Y-%m-%d')}.")
            continue

        assert target_value is not None
        if action == "stop":
            if target_value not in muted_symbols:
                muted_symbols.add(target_value)
                save_muted_symbols(state, alert_type, now, muted_symbols)
                changed = True
            send_confirmation(
                f"Muted {alert_label} alerts for {target_value} on {now.strftime('%Y-%m-%d')}."
            )
        else:
            if target_value in muted_symbols:
                muted_symbols.remove(target_value)
                save_muted_symbols(state, alert_type, now, muted_symbols)
                changed = True
            send_confirmation(
                f"Resumed {alert_label} alerts for {target_value} on {now.strftime('%Y-%m-%d')}."
            )

    return changed
