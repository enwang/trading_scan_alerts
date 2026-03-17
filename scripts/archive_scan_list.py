#!/usr/bin/env python3
"""
Archive the current scan-list to scan-history.jsonl after each build.

Appends a dated JSON line so past days' symbol lists are preserved.

Usage:
    python3 archive_scan_list.py premarket
    python3 archive_scan_list.py postmarket
"""

import sys
import json
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
SCAN_LIST = REPO_DIR / "tv-output" / "scan-list.json"
HISTORY_FILE = REPO_DIR / "tv-output" / "scan-history.jsonl"


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "unknown"

    if not SCAN_LIST.exists():
        print(f"scan-list.json not found, skipping archive", file=sys.stderr)
        return

    with open(SCAN_LIST) as f:
        scan_list = json.load(f)

    phase_data = scan_list.get(phase, {})
    symbols = phase_data.get("symbols", [])
    built_at = phase_data.get("built_at", datetime.now(timezone.utc).isoformat())

    entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "phase": phase,
        "built_at": built_at,
        "symbol_count": len(symbols),
        "symbols": symbols,
    }

    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    print(f"Archived {len(symbols)} [{phase}] symbols to scan-history.jsonl")


if __name__ == "__main__":
    main()
