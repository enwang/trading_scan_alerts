from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from scan_reversal_alert import (
    EASTERN,
    ScanConfig,
    evaluate_reversal_scan,
    resolve_reversal_scan_list,
    should_alert,
)
from telegram_alert_controls import (
    build_help_message,
    is_alert_type_muted,
    load_muted_symbols,
    process_telegram_commands,
    save_muted_symbols,
    set_alert_type_muted,
)


def _ts(hour: int, minute: int) -> int:
    dt = datetime(2026, 3, 2, hour, minute, tzinfo=EASTERN)
    return int(dt.astimezone(UTC).timestamp() * 1000)


class ReversalScanTests(unittest.TestCase):
    def test_reversal_scan_matches_when_stock_recovers_to_previous_reference(self) -> None:
        config = ScanConfig(
            reversal_scan_list=("TEST",),
            premarket_drawdown_pct=6,
            regular_session_rebound_pct=4,
            distance_to_reference_pct=1.5,
            min_regular_session_gain_pct=3,
        )
        bars = [
            {"t": _ts(8, 0), "o": 95.0, "h": 95.2, "l": 89.8, "c": 90.0},
            {"t": _ts(9, 30), "o": 92.0, "h": 93.0, "l": 91.9, "c": 92.5},
            {"t": _ts(11, 0), "o": 95.0, "h": 97.0, "l": 94.8, "c": 96.5},
            {"t": _ts(15, 55), "o": 98.0, "h": 99.4, "l": 97.9, "c": 99.1},
        ]

        result = evaluate_reversal_scan(
            symbol="TEST",
            previous_close=100.0,
            previous_high=101.0,
            bars=bars,
            config=config,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.near_previous_close)
        self.assertFalse(result.near_previous_high)
        self.assertEqual(result.trigger_price, 99.1)

    def test_reversal_scan_skips_if_not_close_enough_to_previous_levels(self) -> None:
        config = ScanConfig(
            reversal_scan_list=("TEST",),
            premarket_drawdown_pct=6,
            regular_session_rebound_pct=4,
            distance_to_reference_pct=1.0,
            min_regular_session_gain_pct=3,
        )
        bars = [
            {"t": _ts(8, 0), "o": 95.0, "h": 95.2, "l": 89.8, "c": 90.0},
            {"t": _ts(9, 30), "o": 92.0, "h": 93.0, "l": 91.9, "c": 92.5},
            {"t": _ts(12, 0), "o": 95.0, "h": 96.5, "l": 94.7, "c": 96.2},
            {"t": _ts(15, 55), "o": 96.0, "h": 97.4, "l": 95.9, "c": 97.2},
        ]

        result = evaluate_reversal_scan(
            symbol="TEST",
            previous_close=100.0,
            previous_high=101.0,
            bars=bars,
            config=config,
        )

        self.assertIsNone(result)

    def test_resolve_reversal_scan_list_uses_built_scan_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            scan_list_path = Path(tmpdir) / "scan-list.json"
            scan_list_path.write_text(json.dumps({
                "postmarket": {"symbols": ["NVDA", "TSLA"]},
                "premarket": {"symbols": ["TSLA", "AMD"]},
            }) + "\n")
            config = ScanConfig(scan_list_path=scan_list_path)

            symbols = resolve_reversal_scan_list(config)

        self.assertEqual(symbols, ("NVDA", "TSLA", "AMD"))

    def test_reversal_should_alert_respects_muted_symbol_and_type(self) -> None:
        config = ScanConfig(
            reversal_scan_list=("TEST",),
            premarket_drawdown_pct=6,
            regular_session_rebound_pct=4,
            distance_to_reference_pct=1.5,
            min_regular_session_gain_pct=3,
        )
        bars = [
            {"t": _ts(8, 0), "o": 95.0, "h": 95.2, "l": 89.8, "c": 90.0},
            {"t": _ts(9, 30), "o": 92.0, "h": 93.0, "l": 91.9, "c": 92.5},
            {"t": _ts(11, 0), "o": 95.0, "h": 97.0, "l": 94.8, "c": 96.5},
            {"t": _ts(15, 55), "o": 98.0, "h": 99.4, "l": 97.9, "c": 99.1},
        ]
        result = evaluate_reversal_scan(
            symbol="TEST",
            previous_close=100.0,
            previous_high=101.0,
            bars=bars,
            config=config,
        )

        assert result is not None
        now = datetime(2026, 3, 2, 15, 55, tzinfo=EASTERN)

        symbol_muted_state: dict[str, object] = {}
        save_muted_symbols(symbol_muted_state, "REVERSAL", now, {"TEST"})
        self.assertEqual(load_muted_symbols(symbol_muted_state, "REVERSAL", now), {"TEST"})
        self.assertFalse(should_alert(result, symbol_muted_state, now))

        type_muted_state: dict[str, object] = {}
        set_alert_type_muted(type_muted_state, "REVERSAL", now, True)
        self.assertTrue(is_alert_type_muted(type_muted_state, "REVERSAL", now))
        self.assertFalse(should_alert(result, type_muted_state, now))

    def test_reversal_telegram_commands_support_help_and_type_mute(self) -> None:
        state: dict[str, object] = {}
        now = datetime(2026, 3, 2, 10, 0, tzinfo=EASTERN)
        updates = [
            {
                "update_id": 40,
                "message": {
                    "chat": {"id": 123},
                    "text": "stop --help",
                },
            },
            {
                "update_id": 41,
                "message": {
                    "chat": {"id": 123},
                    "text": "stop reversal",
                },
            },
            {
                "update_id": 42,
                "message": {
                    "chat": {"id": 123},
                    "text": "resume reversal",
                },
            },
        ]

        from unittest.mock import patch

        with patch("telegram_alert_controls.fetch_telegram_updates", return_value=updates):
            sent_messages: list[str] = []
            changed = process_telegram_commands(
                bot_token="token",
                chat_id="123",
                state=state,
                now=now,
                alert_type="REVERSAL",
                alert_label="reversal",
                alert_aliases={"reversal", "reversals", "rev"},
                help_alias_examples=("reversal",),
                send_confirmation=sent_messages.append,
            )

        self.assertTrue(changed)
        self.assertFalse(is_alert_type_muted(state, "REVERSAL", now))
        self.assertEqual(state["telegram_update_offset"], 43)
        self.assertEqual(sent_messages[0], build_help_message("reversal", ("reversal",)))


if __name__ == "__main__":
    unittest.main()
