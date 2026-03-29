# Alert Backtest Rules

This file defines the default backtest and trade-evaluation rules for alerts in this repo.

It applies to:

- current alert scanners such as `scan_reversal_alert.py`
- current alert scanners such as `scan_undercut_rally_alert.py`
- future alert scanners added to this repo unless the user explicitly overrides the rule

## Default trade model

When evaluating alert quality, use this execution model by default:

- entry: buy stop at the alert trigger price
- stop loss: the alert invalidation level
- `1R` target: `entry + (entry - stop)`
- evaluate outcomes intraday only, using bars after the trigger bar

Classify each alert as one of:

- `target_1R`: price reaches the `1R` target before the stop loss
- `stop`: price reaches the stop loss before the `1R` target
- `open`: neither the stop loss nor the `1R` target is reached by the end of the session
- `ambiguous_same_bar`: both stop and target are touched in the same minute bar after entry, so minute data cannot determine sequence

## Required reporting

When backtesting any alert type, report at minimum:

- alert count
- stop count and stop rate
- `1R` target count and hit rate
- unresolved `open` count
- ambiguous same-bar count
- representative examples of winners and losers

## Scanner-specific setup rules

Each alert type can add its own setup filter before the shared trade model is applied.

Example: current U&R setup under evaluation

- undercut must remain less than `2.0%` below the previous day's low
- trigger requires reclaiming the previous day's low and reaching `2.0%` above that prior low
- the reclaim trigger must happen within `15` regular-session minutes of the first valid undercut

## Notes

- This file is the repo convention for backtests. Future work should use this file as the default evaluation reference unless the user changes the rule.
- Historical tests may still be limited by available market data and by whether historical watchlist membership is stored separately.
