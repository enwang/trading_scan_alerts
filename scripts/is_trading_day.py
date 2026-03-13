#!/usr/bin/env python3
"""
Check if the given date (default: today) is a NYSE trading day.

Usage:
    python3 is_trading_day.py            # check today
    python3 is_trading_day.py 2026-03-17 # check specific date

Exit codes:
    0 = IS a trading day
    1 = NOT a trading day (holiday or weekend)
"""

import sys
from datetime import date

try:
    import exchange_calendars as xcals
except ImportError:
    print("ERROR: exchange_calendars not installed. Run: pip3 install exchange_calendars", file=sys.stderr)
    sys.exit(2)


def is_trading_day(check_date=None):
    if check_date is None:
        check_date = date.today()
    nyse = xcals.get_calendar("XNYS")
    return nyse.is_session(str(check_date))


if __name__ == "__main__":
    check_date = None
    if len(sys.argv) > 1:
        from datetime import datetime
        check_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()

    today = check_date if check_date else date.today()

    if is_trading_day(today):
        print(f"{today} is a NYSE trading day")
        sys.exit(0)
    else:
        print(f"{today} is NOT a NYSE trading day (holiday or weekend)")
        sys.exit(1)
