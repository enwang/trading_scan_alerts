from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scan_reversal_alert import EASTERN
from scan_undercut_rally_alert import (
    ScanConfig,
    evaluate_undercut_rally_scan,
    load_watchlist_symbols,
    mark_alert_sent,
    should_alert,
)
from telegram_alert_controls import (
    build_help_message,
    is_alert_type_muted,
    load_muted_symbols,
    parse_telegram_control_command,
    process_telegram_commands,
    save_muted_symbols,
    set_alert_type_muted,
)


def _ts(hour: int, minute: int) -> int:
    dt = datetime(2026, 3, 2, hour, minute, tzinfo=EASTERN)
    return int(dt.astimezone(UTC).timestamp() * 1000)


class UndercutRallyScanTests(unittest.TestCase):
    def test_load_watchlist_symbols_uses_focus_strong_next_only(self) -> None:
        payload = {
            "watchlists": [
                {"name": "Focus", "symbols": ["NASDAQ:NVDA", "TSLA"]},
                {"name": "Strong", "symbols": ["PLTR", "NASDAQ:NVDA"]},
                {"name": "Holding", "symbols": [], "sections": {"IDEA": ["AMD"], "HOLDING": ["MSFT"]}},
                {"name": "Other", "symbols": ["AAPL"]},
                {"name": "Next", "symbols": ["AMD"]},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "all-lists.json"
            path.write_text(json.dumps(payload) + "\n")
            config = ScanConfig(tradingview_watchlists_path=path)

            symbols = load_watchlist_symbols(config)

        self.assertEqual(symbols, ("NVDA", "TSLA", "PLTR", "AMD", "MSFT"))

    def test_ur_triggers_after_undercut_and_two_percent_rally_from_current_low(self) -> None:
        config = ScanConfig(rebound_pct=2.0)
        bars = [
            {"t": _ts(9, 30), "o": 100.2, "h": 100.4, "l": 99.0, "c": 99.1},
            {"t": _ts(9, 31), "o": 99.1, "h": 99.2, "l": 97.0, "c": 97.4},
            {"t": _ts(9, 32), "o": 97.4, "h": 99.2, "l": 97.2, "c": 98.0},
        ]

        result = evaluate_undercut_rally_scan(
            symbol="TEST",
            previous_low=100.0,
            previous_close=102.0,
            bars=bars,
            config=config,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.undercut_low, 97.0)
        self.assertEqual(result.trigger_price, 99.2)
        self.assertAlmostEqual(result.rebound_from_low_pct, 2.268041237, places=6)

    def test_ur_does_not_trigger_without_two_percent_rally(self) -> None:
        config = ScanConfig(rebound_pct=2.0)
        bars = [
            {"t": _ts(9, 30), "o": 100.0, "h": 100.1, "l": 98.0, "c": 98.4},
            {"t": _ts(9, 31), "o": 98.4, "h": 99.95, "l": 98.1, "c": 98.9},
        ]

        result = evaluate_undercut_rally_scan(
            symbol="TEST",
            previous_low=100.0,
            previous_close=102.0,
            bars=bars,
            config=config,
        )

        self.assertIsNone(result)

    def test_ur_does_not_trigger_without_undercut(self) -> None:
        config = ScanConfig(rebound_pct=2.0)
        bars = [
            {"t": _ts(9, 30), "o": 100.5, "h": 101.0, "l": 100.1, "c": 100.8},
            {"t": _ts(9, 31), "o": 100.8, "h": 101.5, "l": 100.3, "c": 101.2},
        ]

        result = evaluate_undercut_rally_scan(
            symbol="TEST",
            previous_low=100.0,
            previous_close=102.0,
            bars=bars,
            config=config,
        )

        self.assertIsNone(result)

    def test_ur_ignores_premarket_and_postmarket_bars(self) -> None:
        config = ScanConfig(rebound_pct=2.0)
        bars = [
            {"t": _ts(8, 0), "o": 100.0, "h": 101.5, "l": 98.0, "c": 100.8},
            {"t": _ts(9, 30), "o": 100.2, "h": 100.4, "l": 100.1, "c": 100.3},
            {"t": _ts(16, 5), "o": 100.3, "h": 101.8, "l": 97.5, "c": 101.1},
        ]

        result = evaluate_undercut_rally_scan(
            symbol="TEST",
            previous_low=100.0,
            previous_close=102.0,
            bars=bars,
            config=config,
        )

        self.assertIsNone(result)

    def test_alert_dedupe_blocks_repeat_on_same_low_and_trigger_but_allows_new_ur(self) -> None:
        config = ScanConfig(rebound_pct=2.0)
        first_bars = [
            {"t": _ts(9, 30), "o": 100.2, "h": 100.4, "l": 98.0, "c": 98.3},
            {"t": _ts(9, 31), "o": 98.3, "h": 100.1, "l": 98.1, "c": 99.1},
        ]
        first_result = evaluate_undercut_rally_scan(
            symbol="TEST",
            previous_low=100.0,
            previous_close=102.0,
            bars=first_bars,
            config=config,
        )
        second_result_same_low = evaluate_undercut_rally_scan(
            symbol="TEST",
            previous_low=100.0,
            previous_close=102.0,
            bars=first_bars,
            config=config,
        )
        same_low_new_trigger_bars = [
            {"t": _ts(10, 5), "o": 99.5, "h": 99.7, "l": 98.0, "c": 98.4},
            {"t": _ts(10, 6), "o": 98.4, "h": 100.4, "l": 98.1, "c": 99.8},
        ]
        second_result_same_low_new_trigger = evaluate_undercut_rally_scan(
            symbol="TEST",
            previous_low=100.0,
            previous_close=102.0,
            bars=same_low_new_trigger_bars,
            config=config,
        )
        lower_low_bars = [
            {"t": _ts(10, 15), "o": 99.4, "h": 99.5, "l": 97.2, "c": 97.6},
            {"t": _ts(10, 16), "o": 97.6, "h": 99.2, "l": 97.4, "c": 98.3},
        ]
        second_result_lower_low = evaluate_undercut_rally_scan(
            symbol="TEST",
            previous_low=100.0,
            previous_close=102.0,
            bars=lower_low_bars,
            config=config,
        )

        assert first_result is not None
        assert second_result_same_low is not None
        assert second_result_same_low_new_trigger is not None
        assert second_result_lower_low is not None
        state: dict[str, object] = {}
        now = datetime(2026, 3, 2, 10, 0, tzinfo=EASTERN)

        self.assertTrue(should_alert(first_result, state, now))
        mark_alert_sent(first_result, state, now)
        self.assertFalse(should_alert(second_result_same_low, state, now))
        self.assertTrue(should_alert(second_result_same_low_new_trigger, state, now))
        self.assertTrue(should_alert(second_result_lower_low, state, now))

    def test_parse_telegram_control_command_accepts_stop_and_resume(self) -> None:
        aliases = {"ur", "u&r", "undercut", "undercut-rally"}
        self.assertEqual(
            parse_telegram_control_command("stop aapl", alert_aliases=aliases),
            ("stop", "symbol", "AAPL"),
        )
        self.assertEqual(
            parse_telegram_control_command("stop ur", alert_aliases=aliases),
            ("stop", "type", None),
        )
        self.assertEqual(
            parse_telegram_control_command("stop ur msft", alert_aliases=aliases),
            ("stop", "symbol", "MSFT"),
        )
        self.assertEqual(
            parse_telegram_control_command("resume U&R", alert_aliases=aliases),
            ("resume", "type", None),
        )
        self.assertEqual(
            parse_telegram_control_command("resume U&R nvda", alert_aliases=aliases),
            ("resume", "symbol", "NVDA"),
        )
        self.assertEqual(
            parse_telegram_control_command("stop --help", alert_aliases=aliases),
            ("stop", "help", None),
        )
        self.assertIsNone(parse_telegram_control_command("pause aapl", alert_aliases=aliases))
        self.assertIsNone(parse_telegram_control_command("stop", alert_aliases=aliases))

    def test_should_alert_respects_muted_symbols(self) -> None:
        config = ScanConfig(rebound_pct=2.0)
        bars = [
            {"t": _ts(9, 30), "o": 100.2, "h": 100.4, "l": 98.0, "c": 98.3},
            {"t": _ts(9, 31), "o": 98.3, "h": 100.1, "l": 98.1, "c": 99.1},
        ]
        result = evaluate_undercut_rally_scan(
            symbol="AAPL",
            previous_low=100.0,
            previous_close=102.0,
            bars=bars,
            config=config,
        )

        assert result is not None
        state: dict[str, object] = {}
        now = datetime(2026, 3, 2, 10, 0, tzinfo=EASTERN)
        save_muted_symbols(state, "U&R", now, {"AAPL"})

        self.assertEqual(load_muted_symbols(state, "U&R", now), {"AAPL"})
        self.assertFalse(should_alert(result, state, now))

    def test_should_alert_respects_muted_alert_type(self) -> None:
        config = ScanConfig(rebound_pct=2.0)
        bars = [
            {"t": _ts(9, 30), "o": 100.2, "h": 100.4, "l": 98.0, "c": 98.3},
            {"t": _ts(9, 31), "o": 98.3, "h": 100.1, "l": 98.1, "c": 99.1},
        ]
        result = evaluate_undercut_rally_scan(
            symbol="AAPL",
            previous_low=100.0,
            previous_close=102.0,
            bars=bars,
            config=config,
        )

        assert result is not None
        state: dict[str, object] = {}
        now = datetime(2026, 3, 2, 10, 0, tzinfo=EASTERN)
        set_alert_type_muted(state, "U&R", now, True)

        self.assertTrue(is_alert_type_muted(state, "U&R", now))
        self.assertFalse(should_alert(result, state, now))

    def test_process_telegram_commands_updates_muted_symbols(self) -> None:
        config = ScanConfig(
            telegram_bot_token="token",
            telegram_chat_id="123",
        )
        state: dict[str, object] = {}
        now = datetime(2026, 3, 2, 10, 0, tzinfo=EASTERN)

        updates = [
            {
                "update_id": 10,
                "message": {
                    "chat": {"id": 123},
                    "text": "stop ur aapl",
                },
            },
            {
                "update_id": 11,
                "message": {
                    "chat": {"id": 123},
                    "text": "resume AAPL",
                },
            },
        ]

        from unittest.mock import patch

        with patch("telegram_alert_controls.fetch_telegram_updates", return_value=updates):
            sent_messages: list[str] = []
            changed = process_telegram_commands(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                state=state,
                now=now,
                alert_type="U&R",
                alert_label="U&R",
                alert_aliases={"ur", "u&r", "undercut", "undercut-rally"},
                help_alias_examples=("ur",),
                send_confirmation=sent_messages.append,
            )

        self.assertTrue(changed)
        self.assertEqual(load_muted_symbols(state, "U&R", now), set())
        self.assertEqual(state["telegram_update_offset"], 12)
        self.assertEqual(len(sent_messages), 2)

    def test_process_telegram_commands_updates_muted_alert_type(self) -> None:
        config = ScanConfig(
            telegram_bot_token="token",
            telegram_chat_id="123",
        )
        state: dict[str, object] = {}
        now = datetime(2026, 3, 2, 10, 0, tzinfo=EASTERN)

        updates = [
            {
                "update_id": 20,
                "message": {
                    "chat": {"id": 123},
                    "text": "stop ur",
                },
            },
            {
                "update_id": 21,
                "message": {
                    "chat": {"id": 123},
                    "text": "resume ur",
                },
            },
        ]

        from unittest.mock import patch

        with patch("telegram_alert_controls.fetch_telegram_updates", return_value=updates):
            sent_messages: list[str] = []
            changed = process_telegram_commands(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                state=state,
                now=now,
                alert_type="U&R",
                alert_label="U&R",
                alert_aliases={"ur", "u&r", "undercut", "undercut-rally"},
                help_alias_examples=("ur",),
                send_confirmation=sent_messages.append,
            )

        self.assertTrue(changed)
        self.assertFalse(is_alert_type_muted(state, "U&R", now))
        self.assertEqual(state["telegram_update_offset"], 22)
        self.assertEqual(len(sent_messages), 2)

    def test_process_telegram_commands_replies_with_help(self) -> None:
        config = ScanConfig(
            telegram_bot_token="token",
            telegram_chat_id="123",
        )
        state: dict[str, object] = {}
        now = datetime(2026, 3, 2, 10, 0, tzinfo=EASTERN)
        updates = [
            {
                "update_id": 30,
                "message": {
                    "chat": {"id": 123},
                    "text": "stop --help",
                },
            },
        ]

        from unittest.mock import patch

        with patch("telegram_alert_controls.fetch_telegram_updates", return_value=updates):
            sent_messages: list[str] = []
            changed = process_telegram_commands(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                state=state,
                now=now,
                alert_type="U&R",
                alert_label="U&R",
                alert_aliases={"ur", "u&r", "undercut", "undercut-rally"},
                help_alias_examples=("ur",),
                send_confirmation=sent_messages.append,
            )

        self.assertTrue(changed)
        self.assertEqual(sent_messages, [build_help_message("U&R", ("ur",))])


if __name__ == "__main__":
    unittest.main()
