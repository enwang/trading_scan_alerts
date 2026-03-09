import test from "node:test";
import assert from "node:assert/strict";

import {
  parseWatchlistFromLines,
  symbolFromScreenerHref,
  parseTvWsFrame,
} from "../scripts/tradingview_automation.mjs";

test("watchlist parser keeps section grouping and captures full section symbols", () => {
  const text = `
Watchlist
Symbol
Last
Chg
Chg%
INDICES
S
SPX
6,784.93
-96.70
-1.41%
N
NDQ
24,609.86
-382.74
-1.53%
D
DJI
48,228.11
-676.67
-1.38%
V
VIX
D
24.38
2.94
13.71%
D
DXY
99.229
0.681
0.69%
STOCKS
A
AAPL
261.88
-2.84
-1.07%
T
TSLA
390.95
-12.37
-3.07%
N
NFLX
96.70
-0.39
-0.40%
`;

  const parsed = parseWatchlistFromLines(text);

  assert.deepEqual(parsed.sections?.INDICES, ["SPX", "NDQ", "DJI", "VIX", "DXY"]);
  assert.deepEqual(parsed.sections?.STOCKS, ["AAPL", "TSLA", "NFLX"]);
  assert.deepEqual(parsed.symbols, ["SPX", "NDQ", "DJI", "VIX", "DXY", "AAPL", "TSLA", "NFLX"]);
});

test("watchlist parser supports flat lists without section headers", () => {
  const text = `
N
NVDA
150.23
1.04
0.69%
T
TSLA
390.95
-12.37
-3.07%
`;

  const parsed = parseWatchlistFromLines(text);
  assert.equal(parsed.sections, undefined);
  assert.deepEqual(parsed.symbols, ["NVDA", "TSLA"]);
});

test("screener href parser normalizes TradingView symbol urls", () => {
  assert.equal(symbolFromScreenerHref("/symbols/NASDAQ-AAPL/"), "NASDAQ:AAPL");
  assert.equal(symbolFromScreenerHref("/symbols/BINANCE-BTCUSDT/"), "BINANCE:BTCUSDT");
  assert.equal(symbolFromScreenerHref("/foo/bar"), null);
});

test("ws frame parser decodes multiple ~m~ framed json messages", () => {
  const framed =
    '~m~45~m~{"m":"quote_add_symbols","p":["watchlist", "NASDAQ:AAPL"]}' +
    '~m~39~m~{"m":"quote_fast_symbols","p":["watchlist"]}';

  const messages = parseTvWsFrame(framed);
  assert.equal(messages.length, 2);
  assert.equal(messages[0].m, "quote_add_symbols");
  assert.equal(messages[1].m, "quote_fast_symbols");
});
