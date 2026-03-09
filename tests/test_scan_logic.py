from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
import unittest

from scan_reversal_alert import (
    EASTERN,
    ScanConfig,
    evaluate_reversal_scan,
    load_tradingview_watchlist_symbols,
    resolve_reversal_scan_list,
)


def _ts(hour: int, minute: int) -> int:
    dt = datetime(2026, 3, 2, hour, minute, tzinfo=EASTERN)
    return int(dt.astimezone(UTC).timestamp() * 1000)


class ReversalScanTests(unittest.TestCase):
    def test_reversal_scan_matches_when_stock_recovers_to_previous_reference(self) -> None:
        config = ScanConfig(
            polygon_api_key="test",
            reversal_scan_list=("TEST",),
            premarket_drawdown_pct=6,
            regular_session_rebound_pct=10,
            distance_to_reference_pct=1.5,
            min_regular_session_gain_pct=3,
        )
        bars = [
            {"t": _ts(8, 0), "o": 95.0, "h": 95.2, "l": 89.8, "c": 90.0},
            {"t": _ts(9, 29), "o": 90.1, "h": 90.2, "l": 89.9, "c": 90.0},
            {"t": _ts(9, 30), "o": 92.0, "h": 93.0, "l": 91.9, "c": 92.5},
            {"t": _ts(11, 0), "o": 95.0, "h": 97.0, "l": 94.8, "c": 96.5},
            {"t": _ts(15, 55), "o": 98.0, "h": 99.4, "l": 97.9, "c": 99.1},
        ]

        result = evaluate_reversal_scan(
            symbol="TEST",
            previous_close=100.0,
            previous_high=101.0,
            bars=bars,
            now=datetime(2026, 3, 2, 15, 55, tzinfo=EASTERN),
            config=config,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.near_previous_close)
        self.assertFalse(result.near_previous_high)
        self.assertEqual(result.trigger_price, 99.1)

    def test_reversal_scan_skips_if_not_close_enough_to_previous_levels(self) -> None:
        config = ScanConfig(
            polygon_api_key="test",
            reversal_scan_list=("TEST",),
            premarket_drawdown_pct=6,
            regular_session_rebound_pct=10,
            distance_to_reference_pct=1,
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
            now=datetime(2026, 3, 2, 15, 55, tzinfo=EASTERN),
            config=config,
        )

        self.assertIsNone(result)

    def test_reversal_scan_triggers_when_price_reclaims_reference_then_keeps_running(self) -> None:
        config = ScanConfig(
            polygon_api_key="test",
            reversal_scan_list=("TEST",),
            premarket_drawdown_pct=6,
            regular_session_rebound_pct=10,
            distance_to_reference_pct=1.5,
            min_regular_session_gain_pct=3,
        )
        bars = [
            {"t": _ts(8, 0), "o": 95.0, "h": 95.2, "l": 89.8, "c": 90.0},
            {"t": _ts(9, 30), "o": 92.0, "h": 93.0, "l": 91.9, "c": 92.5},
            {"t": _ts(11, 30), "o": 98.0, "h": 100.5, "l": 97.8, "c": 99.8},
            {"t": _ts(15, 55), "o": 108.0, "h": 110.0, "l": 107.5, "c": 109.5},
        ]

        result = evaluate_reversal_scan(
            symbol="TEST",
            previous_close=100.0,
            previous_high=101.0,
            bars=bars,
            now=datetime(2026, 3, 2, 15, 55, tzinfo=EASTERN),
            config=config,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.trigger_price, 99.8)

    def test_load_tradingview_watchlist_symbols_reads_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            watchlist_path = Path(tmpdir) / "watchlist.json"
            watchlist_path.write_text('{"symbols":["nvda"," TSLA ","NVDA","","pltr"]}\n')
            config = ScanConfig(
                polygon_api_key="test",
                reversal_scan_list=(),
                tradingview_watchlist_enabled=True,
                tradingview_watchlist_path=watchlist_path,
            )

            symbols = load_tradingview_watchlist_symbols(config)

            self.assertEqual(symbols, ("NVDA", "TSLA", "PLTR"))

    def test_resolve_reversal_scan_list_merges_manual_watchlist_and_prefilter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            watchlist_path = Path(tmpdir) / "watchlist.json"
            watchlist_path.write_text('{"symbols":["TSLA","PLTR"]}\n')
            config = ScanConfig(
                polygon_api_key="test",
                reversal_scan_list=("NVDA", "TSLA"),
                tradingview_watchlist_enabled=True,
                tradingview_watchlist_path=watchlist_path,
            )

            class StubClient:
                pass

            import scan_reversal_alert as module

            original = module.build_prefilter_symbol_list
            module.build_prefilter_symbol_list = lambda client, config, now: ("PLTR", "AMD")
            try:
                symbols = resolve_reversal_scan_list(
                    StubClient(),
                    config,
                    datetime(2026, 3, 2, 10, 0, tzinfo=EASTERN),
                )
            finally:
                module.build_prefilter_symbol_list = original

            self.assertEqual(symbols, ("NVDA", "TSLA", "PLTR", "AMD"))

    def test_load_tradingview_watchlist_symbols_raises_when_missing(self) -> None:
        config = ScanConfig(
            polygon_api_key="test",
            reversal_scan_list=(),
            tradingview_watchlist_enabled=True,
            tradingview_watchlist_path=Path("missing-watchlist.json"),
        )

        with self.assertRaisesRegex(ValueError, "TradingView watchlist file not found"):
            load_tradingview_watchlist_symbols(config)


if __name__ == "__main__":
    unittest.main()
