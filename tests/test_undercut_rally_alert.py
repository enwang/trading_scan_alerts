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

    def test_ur_triggers_on_reclaim_of_previous_low(self) -> None:
        # Stock undercuts, then reclaims previous_low + 0.5% buffer
        config = ScanConfig(rebound_pct=0.5)
        bars = [
            {"t": _ts(9, 30), "o": 100.2, "h": 100.4, "l": 99.0, "c": 99.1},   # below support (1)
            {"t": _ts(9, 31), "o": 99.1, "h": 99.5,  "l": 97.0, "c": 97.4},    # lower low    (2)
            {"t": _ts(9, 32), "o": 97.4, "h": 100.6, "l": 100.1, "c": 100.5},  # fully above, h>100.5
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
        self.assertEqual(result.trigger_price, 100.6)
        self.assertAlmostEqual(result.rebound_from_low_pct, 3.711340, places=4)

    def test_ur_does_not_trigger_below_support(self) -> None:
        # Stock undercuts 3%, bounces 2% from low — still below previous_low (dead-cat bounce)
        config = ScanConfig(rebound_pct=0.5)
        bars = [
            {"t": _ts(9, 30), "o": 100.2, "h": 100.4, "l": 99.0, "c": 98.3},  # below support (1)
            {"t": _ts(9, 31), "o": 98.3, "h": 98.5,  "l": 97.0, "c": 97.8},   # lower low     (2)
            {"t": _ts(9, 32), "o": 97.8, "h": 99.0,  "l": 98.0, "c": 98.5},   # still below support
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
        config = ScanConfig(rebound_pct=0.5)
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
        config = ScanConfig(rebound_pct=0.5)
        bars = [
            {"t": _ts(8, 0),  "o": 100.0, "h": 101.5, "l": 98.0, "c": 100.8},  # pre-market
            {"t": _ts(9, 30), "o": 100.2, "h": 100.4, "l": 100.1, "c": 100.3},  # regular, above support
            {"t": _ts(16, 5), "o": 100.3, "h": 101.8, "l": 97.5, "c": 101.1},   # post-market
        ]

        result = evaluate_undercut_rally_scan(
            symbol="TEST",
            previous_low=100.0,
            previous_close=102.0,
            bars=bars,
            config=config,
        )

        self.assertIsNone(result)

    def test_ur_single_bar_shakeout_can_trigger(self) -> None:
        config = ScanConfig(rebound_pct=0.5)
        bars = [
            {"t": _ts(9, 30), "o": 100.2, "h": 100.4, "l": 99.0, "c": 99.5},
            {"t": _ts(9, 31), "o": 99.5, "h": 100.8, "l": 100.1, "c": 100.6},  # reclaim, h=100.8>100.5
        ]

        result = evaluate_undercut_rally_scan(
            symbol="TEST",
            previous_low=100.0,
            previous_close=102.0,
            bars=bars,
            config=config,
        )

        self.assertIsNotNone(result)

    def test_alert_dedupe_blocks_repeat_on_same_low_but_allows_new_lower_low(self) -> None:
        config = ScanConfig(rebound_pct=0.5)
        # First sequence: undercut, then reclaim
        first_bars = [
            {"t": _ts(9, 30), "o": 100.2, "h": 100.4, "l": 98.0, "c": 98.3},   # below (1)
            {"t": _ts(9, 31), "o": 98.3, "h": 98.5,  "l": 97.5, "c": 98.1},    # lower low (2)
            {"t": _ts(9, 32), "o": 98.1, "h": 100.6, "l": 100.1, "c": 100.4},  # reclaim
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
        # Second sequence: deeper undercut, then reclaim
        lower_low_bars = [
            {"t": _ts(10, 15), "o": 99.4, "h": 99.5, "l": 96.0, "c": 96.5},    # deeper (1)
            {"t": _ts(10, 16), "o": 96.5, "h": 96.8, "l": 95.5, "c": 96.0},    # even lower (2)
            {"t": _ts(10, 17), "o": 96.0, "h": 100.6, "l": 100.0, "c": 100.5}, # reclaim (l==previous_low ok)
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
        assert second_result_lower_low is not None
        state: dict[str, str] = {}
        now = datetime(2026, 3, 2, 10, 0, tzinfo=EASTERN)

        self.assertTrue(should_alert(first_result, state, now))
        mark_alert_sent(first_result, state, now)
        self.assertFalse(should_alert(second_result_same_low, state, now))
        self.assertTrue(should_alert(second_result_lower_low, state, now))


if __name__ == "__main__":
    unittest.main()
