#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import os from "node:os";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright-core";

const ROOT = process.cwd();

function parseDotenv(text) {
  const env = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const separatorIndex = line.indexOf("=");
    if (separatorIndex === -1) continue;
    const key = line.slice(0, separatorIndex).trim();
    let value = line.slice(separatorIndex + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (key && process.env[key] === undefined) {
      env[key] = value;
    }
  }
  return env;
}

async function loadDotenv() {
  const envPath = path.join(ROOT, ".env");
  try {
    const text = await fs.readFile(envPath, "utf8");
    Object.assign(process.env, parseDotenv(text));
  } catch (error) {
    if (error && error.code !== "ENOENT") {
      throw error;
    }
  }
}

const PROFILE_DIR = path.join(ROOT, ".tradingview-profile");
const OUTPUT_DIR = path.join(ROOT, "tv-output");
const command = process.argv[2] || "help";

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

async function pathExists(target) {
  try {
    await fs.access(target);
    return true;
  } catch {
    return false;
  }
}

async function prepareUserDataDir() {
  const useAppProfile =
    (process.env.TRADINGVIEW_USE_APP_PROFILE || "true").trim().toLowerCase() in
    { "1": true, true: true, yes: true, on: true };
  const appProfileDir =
    process.env.TRADINGVIEW_APP_PROFILE_DIR ||
    path.join(process.env.HOME || "", "Library/Application Support/TradingView");

  if (!useAppProfile) {
    await ensureDir(PROFILE_DIR);
    return { userDataDir: PROFILE_DIR, cleanup: async () => {} };
  }

  if (!(await pathExists(appProfileDir))) {
    throw new Error(`TradingView app profile not found: ${appProfileDir}`);
  }

  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "tradingview-app-profile-"));
  const LOCK_FILES = new Set(["LOCK", "SingletonLock", "SingletonSocket", "SingletonCookie"]);

  // Detect whether the app profile uses a flat layout (Electron-style: cookies at root)
  // vs a Chrome-style layout (cookies inside a Default/ subfolder).
  // Chrome requires the user-data-dir to contain a Default/ subfolder.
  const hasCookiesAtRoot = await pathExists(path.join(appProfileDir, "Cookies"));
  const hasCookiesInDefault = await pathExists(path.join(appProfileDir, "Default", "Cookies"));

  if (hasCookiesAtRoot && !hasCookiesInDefault) {
    // Flat Electron layout — copy into Default/ so Chrome can find the session
    const destDefault = path.join(tempDir, "Default");
    await fs.mkdir(destDefault, { recursive: true });
    await fs.cp(appProfileDir, destDefault, {
      recursive: true,
      force: true,
      filter: (source) => !LOCK_FILES.has(path.basename(source)),
    });
  } else {
    // Chrome-style layout — copy as-is
    await fs.cp(appProfileDir, tempDir, {
      recursive: true,
      force: true,
      filter: (source) => !LOCK_FILES.has(path.basename(source)),
    });
  }

  return {
    userDataDir: tempDir,
    cleanup: async () => {
      await fs.rm(tempDir, { recursive: true, force: true });
    },
  };
}

async function launchBrowser({ headed = false } = {}) {
  const profile = await prepareUserDataDir();
  const chromePath =
    process.env.TRADINGVIEW_CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const context = await chromium.launchPersistentContext(profile.userDataDir, {
    executablePath: chromePath,
    headless: !headed,
    viewport: { width: 1600, height: 1000 },
  });
  context._cleanupProfile = profile.cleanup;
  return context;
}

async function closeContext(context) {
  try {
    await context.close();
  } finally {
    if (typeof context._cleanupProfile === "function") {
      await context._cleanupProfile();
    }
  }
}

async function getPage(context) {
  const existing = context.pages();
  return existing[0] || context.newPage();
}

async function isLikelyLoggedOut(page) {
  const body = (await page.locator("body").innerText()).toLowerCase();
  return body.includes("get started") && body.includes("sign in");
}

async function loginFlow() {
  const context = await launchBrowser({ headed: true });
  const page = await getPage(context);
  await page.goto("https://www.tradingview.com/chart/", {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  console.log("TradingView opened in Chrome.");
  console.log("Log in manually if needed, then press Enter here to save the session and close the browser.");
  await new Promise((resolve) => {
    process.stdin.resume();
    process.stdin.once("data", () => resolve());
  });
  await closeContext(context);
}

function tickerFromText(text) {
  const match = text.trim().match(/\b[A-Z][A-Z0-9.-]{0,9}\b/);
  return match ? match[0] : null;
}

function normalizeSymbol(raw, { allowSingleLetter = false } = {}) {
  if (typeof raw !== "string") return null;
  let value = raw.trim().toUpperCase();
  if (!value) return null;

  if (value.includes(":")) {
    const parts = value.split(":");
    if (parts.length !== 2) return null;
    const [exchange, symbol] = parts;
    if (!/^[A-Z0-9_-]{1,15}$/.test(exchange)) return null;
    if (!/^[A-Z][A-Z0-9.!-]{0,14}$/.test(symbol)) return null;
    return `${exchange}:${symbol}`;
  }

  if (!/^[A-Z][A-Z0-9.!-]{0,14}$/.test(value)) return null;
  if (!allowSingleLetter && value.length === 1) return null;

  const blocked = new Set([
    "ADJ", "AFTERHOURS", "CLOSED", "CUSIP", "EXTENDED",
    "FIGI", "H", "ISIN", "L", "MARKET", "NASDAQ", "NYSE",
    "O", "OPEN", "PREMARKET", "R", "SESSION", "SYMBOL", "YTD",
  ]);
  if (blocked.has(value)) return null;
  if (/[0-9]\.[0-9]/.test(value)) return null;
  // Bare symbols longer than 8 chars are almost certainly display artifacts
  // (US exchange tickers max 5 chars; crypto pairs max 6; SPAC warrants max 7-8)
  if (!value.includes(":") && value.length > 8) return null;

  return value;
}

function isSectionHeader(line) {
  // Keep this conservative so symbols like VIX are not mistaken for section names.
  return /^[A-Z][A-Z0-9 &/_-]{3,}$/.test(line) && !/%/.test(line);
}

function parseWatchlistFromLines(text) {
  const lines = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const headers = new Set(["WATCHLIST", "SYMBOL", "LAST", "CHG", "CHG%"]);
  const sections = {};
  let currentSection = null;

  for (let index = 0; index < lines.length - 1; index += 1) {
    const line = lines[index].toUpperCase();
    if (headers.has(line)) continue;
    const nextLine = (lines[index + 1] || "").toUpperCase();
    if (isSectionHeader(line) && /^[A-Z]$/.test(nextLine)) {
      currentSection = line;
      sections[currentSection] ||= [];
      continue;
    }
    if (!/^[A-Z]$/.test(line)) continue;

    const candidate = normalizeSymbol(lines[index + 1]);
    if (!candidate) continue;
    // Flat list (no section header seen yet) — bucket under a hidden key
    const sec = currentSection ?? "_";
    sections[sec] ||= [];
    if (!sections[sec].includes(candidate)) {
      sections[sec].push(candidate);
    }
  }

  const symbols = Object.values(sections).flat();
  // Strip the hidden flat-list bucket from the sections output
  const namedSections = Object.fromEntries(
    Object.entries(sections).filter(([k]) => k !== "_")
  );
  return { symbols, sections: Object.keys(namedSections).length > 0 ? namedSections : undefined };
}

async function extractSymbolsFromPage(page) {
  const items = await page.evaluate(() => {
    const out = [];
    const selectors = [
      "[data-name*='watchlist' i]",
      "[class*='watchlist' i]",
      "[aria-label*='watchlist' i]",
      "[data-widget-type*='watchlist' i]",
    ].join(",");
    const containers = Array.from(document.querySelectorAll(selectors));
    const isNumericLike = (text) => /[+-]?\d/.test(text);

    for (const container of containers) {
      const rows = Array.from(
        container.querySelectorAll("[role='row'], tr, [data-row-key], [class*='row' i]")
      );
      for (const row of rows) {
        const cells = Array.from(
          row.querySelectorAll("[role='gridcell'], [role='cell'], td, [class*='cell' i]")
        )
          .map((cell) => (cell.textContent || "").trim())
          .filter(Boolean);
        if (cells.length < 2) continue;
        const symbolCell = cells[0] || "";
        const numericCells = cells.slice(1).filter(isNumericLike);
        const href =
          row.querySelector("a[href*='/symbols/']")?.getAttribute("href") ||
          row.closest("a[href*='/symbols/']")?.getAttribute("href") ||
          "";
        const attrs = [
          row.getAttribute("data-symbol"),
          row.getAttribute("data-symbol-full"),
          row.getAttribute("data-symbol-short"),
          row.getAttribute("data-ticker"),
          row.querySelector("[data-symbol]")?.getAttribute("data-symbol"),
          row.querySelector("[data-symbol-full]")?.getAttribute("data-symbol-full"),
          row.querySelector("[data-ticker]")?.getAttribute("data-ticker"),
        ].filter(Boolean);

        if (numericCells.length >= 2 || href || attrs.length > 0) {
          out.push({
            source: "watchlist-row",
            text: symbolCell,
            href,
            attrs,
            cells,
          });
        }
      }
    }

    return out;
  });

  const symbols = [];
  const seen = new Set();
  for (const item of items) {
    const candidates = [
      item.href?.match(/\/symbols\/([^/?#]+)/)?.[1] ?? null,
      ...(item.attrs || []),
      ...String(item.text || "")
        .split(/[\s,/|]+/)
        .map((part) => part.trim()),
      tickerFromText(String(item.text || "")),
    ];

    for (const candidate of candidates) {
      const symbol = normalizeSymbol(candidate, {
        allowSingleLetter: item.source === "watchlist-link" || item.source === "watchlist-attr",
      });
      if (!symbol || seen.has(symbol)) continue;
      seen.add(symbol);
      symbols.push(symbol);
    }
  }

  let parsedSections = null;

  if (symbols.length === 0) {
    const widgetSymbols = await page.evaluate(() => {
      const containers = Array.from(
        document.querySelectorAll(
          ".widgetbar-widget-watchlist, .watchlist-__KRxuOy, [data-name='symbol-list-wrap']"
        )
      );
      const out = [];

      for (const container of containers) {
        const symbolNodes = Array.from(
          container.querySelectorAll(
            "[class*='symbolNameText-'], [data-symbol], [data-ticker], a[href*='/symbols/']"
          )
        );
        for (const node of symbolNodes) {
          const text = (node.innerText || node.textContent || "").trim();
          if (text) {
            out.push(text);
          }
          const href = node.getAttribute?.("href");
          if (href) {
            out.push(href);
          }
          const dataSymbol = node.getAttribute?.("data-symbol");
          if (dataSymbol) {
            out.push(dataSymbol);
          }
          const dataTicker = node.getAttribute?.("data-ticker");
          if (dataTicker) {
            out.push(dataTicker);
          }
        }

        const innerText = (container.innerText || "").trim();
        if (innerText) {
          out.push({ type: "lines", text: innerText });
        }
      }

      return out;
    });

    const linePayload = widgetSymbols.find(
      (item) => item && typeof item === "object" && item.type === "lines"
    );

    if (linePayload) {
      const parsed = parseWatchlistFromLines(linePayload.text);
      parsedSections = parsed.sections;
      const preferredSymbols = parsed.symbols;
      for (const candidate of preferredSymbols) {
        if (seen.has(candidate)) continue;
        seen.add(candidate);
        symbols.push(candidate);
      }
    }

    for (const item of widgetSymbols) {
      if (item && typeof item === "object" && item.type === "lines") {
        continue;
      }
      if (parsedSections) {
        continue;
      }

      const raw = String(item || "");
      const candidates = [
        raw.match(/\/symbols\/([^/?#]+)/)?.[1] ?? null,
        ...raw.split(/[\s,/|]+/).map((part) => part.trim()),
      ];

      for (const candidate of candidates) {
        const symbol = normalizeSymbol(candidate);
        if (!symbol || seen.has(symbol)) continue;
        seen.add(symbol);
        symbols.push(symbol);
      }
    }
  }

  return { count: symbols.length, sections: parsedSections || undefined, symbols };
}

// Grab innerText from the watchlist panel
async function getWatchlistInnerText(page) {
  return page.evaluate(() => {
    const sels = [
      ".widgetbar-widget-watchlist",
      "[data-name='symbol-list-wrap']",
      "[class*='watchlist']",
    ];
    for (const sel of sels) {
      const el = document.querySelector(sel);
      if (el) return el.innerText || "";
    }
    return "";
  });
}

// Scroll through a virtual-scroll watchlist and collect all symbols.
// Clicks to focus the panel, scrolls top→bottom, then bottom→top to
// ensure lazy-loaded items at both ends are captured.
async function extractAllSymbolsScrolling(page) {
  const allSymbols = new Set();
  let parsedSections = undefined;

  // Find the watchlist panel center for mouse events
  const box = await page.evaluate(() => {
    const sels = [
      ".widgetbar-widget-watchlist",
      "[data-name='symbol-list-wrap']",
    ];
    for (const sel of sels) {
      const el = document.querySelector(sel);
      if (el) {
        const r = el.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      }
    }
    return { x: 1500, y: 500 };
  });

  const collect = async () => {
    const partial = await extractSymbolsFromPage(page);
    if (partial.sections && !parsedSections) parsedSections = partial.sections;
    const rawText = await getWatchlistInnerText(page);
    const innerParsed = parseWatchlistFromLines(rawText);
    if (innerParsed.sections && !parsedSections) parsedSections = innerParsed.sections;
    const before = allSymbols.size;
    for (const s of partial.symbols) allSymbols.add(s);
    for (const s of innerParsed.symbols) allSymbols.add(s);
    return allSymbols.size - before;
  };

  // Click panel to give it focus, then scroll to top
  await page.mouse.click(box.x, box.y);
  await page.waitForTimeout(300);
  await page.mouse.move(box.x, box.y);
  await page.mouse.wheel(0, -99999);
  await page.waitForTimeout(900);

  // Pass 1: scroll top → bottom
  let noNewStreak = 0;
  while (noNewStreak < 8) {
    const added = await collect();
    if (added === 0) noNewStreak++;
    else noNewStreak = 0;
    await page.mouse.move(box.x, box.y);
    await page.mouse.wheel(0, 600);
    await page.waitForTimeout(700);
  }

  // Pass 2: scroll bottom → top (catches items lazy-loaded from the bottom)
  await page.mouse.move(box.x, box.y);
  await page.mouse.wheel(0, 99999);
  await page.waitForTimeout(800);
  noNewStreak = 0;
  while (noNewStreak < 6) {
    const added = await collect();
    if (added === 0) noNewStreak++;
    else noNewStreak = 0;
    await page.mouse.move(box.x, box.y);
    await page.mouse.wheel(0, -600);
    await page.waitForTimeout(700);
  }

  // Remove bare symbols that are already represented by a qualified EXCHANGE:SYMBOL
  // (avoids "AMZN" and "NASDAQ:AMZN" both appearing in the output)
  const qualifiedTickers = new Set(
    [...allSymbols].filter((s) => s.includes(":")).map((s) => s.split(":")[1])
  );
  const symbols = [...allSymbols].filter(
    (s) => s.includes(":") || !qualifiedTickers.has(s)
  );

  return { count: symbols.length, sections: parsedSections, symbols };
}

async function collectWatchlist(page) {
  await page.goto("https://www.tradingview.com/chart/", {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await page.waitForTimeout(5000);
  const data = await extractSymbolsFromPage(page);
  return { source: "chart-watchlist", url: page.url(), ...data };
}

// Parse TradingView's WebSocket wire format: ~m~LENGTH~m~JSON~m~LENGTH~m~JSON...
function parseTvWsFrame(text) {
  const msgs = [];
  const parts = text.split(/~m~\d+~m~/);
  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    try { msgs.push(JSON.parse(trimmed)); } catch { /* ignore non-JSON segments */ }
  }
  return msgs;
}

async function collectAllWatchlists(page) {
  // Intercept WebSocket sent frames to capture quote_add_symbols for the watchlist session.
  // TradingView sends the FULL symbol list in a single WS message when you switch to a list.
  const wsListSymbols = new Map(); // listName -> Set<"EXCHANGE:SYMBOL">
  let activeListCapture = null; // set to the list name we just clicked

  page.on("websocket", (ws) => {
    ws.on("framesent", ({ payload }) => {
      const text = typeof payload === "string" ? payload : Buffer.from(payload).toString();
      if (!text.includes("watchlist")) return;
      for (const msg of parseTvWsFrame(text)) {
        if (msg.m !== "quote_add_symbols" && msg.m !== "quote_fast_symbols") continue;
        const params = msg.p;
        if (!Array.isArray(params) || !String(params[0]).includes("watchlist")) continue;
        const symbols = params.slice(1).filter(
          (s) => typeof s === "string" && s.includes(":")
        );
        if (symbols.length === 0) continue;
        const target = activeListCapture;
        if (!target) continue;
        if (!wsListSymbols.has(target)) wsListSymbols.set(target, new Set());
        for (const s of symbols) wsListSymbols.get(target).add(s);
      }
    });
  });

  await page.goto("https://www.tradingview.com/chart/", {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await page.waitForTimeout(5000);

  // --- Try to find list names via "Open list..." dialog ---
  const headerSelectors = [
    "[data-name='watchlist-widget-list-name']",
    "[class*='listTitle']",
    ".widgetbar-widget-watchlist [class*='title']",
    "[data-widget-type='watchlist'] [class*='header'] button",
    "[data-widget-type='watchlist'] [class*='title']",
  ];

  let headerLocator = null;
  for (const sel of headerSelectors) {
    const loc = page.locator(sel).first();
    try {
      if (await loc.isVisible({ timeout: 2000 })) {
        headerLocator = loc;
        console.log(`Watchlist header found via: ${sel}`);
        break;
      }
    } catch { /* try next */ }
  }

  // Get the watchlist name currently visible BEFORE opening context menu
  let currentListName = "default";
  if (headerLocator) {
    try {
      currentListName = (await headerLocator.innerText()).trim() || "default";
    } catch { /* ignore */ }
  }

  let listNames = [];

  if (headerLocator) {
    // Open context menu
    await headerLocator.click();
    await page.waitForTimeout(1000);

    // Click "Open list..."
    const openListLocs = [
      page.getByText("Open list…", { exact: true }),
      page.getByText("Open list...", { exact: true }),
      page.locator("text=Open list").first(),
    ];
    let openListClicked = false;
    for (const loc of openListLocs) {
      try {
        if (await loc.isVisible({ timeout: 1500 })) {
          await loc.click();
          openListClicked = true;
          console.log("Clicked 'Open list...'");
          break;
        }
      } catch { /* try next */ }
    }

    if (openListClicked) {
      await page.waitForTimeout(2000);

      // Save screenshot for diagnostics
      await page.screenshot({ path: path.join(OUTPUT_DIR, "debug-list-picker.png") });

      // Find the "Watchlists" dialog container directly — more reliable than before/after diff
      listNames = await page.evaluate(() => {
        // Identify watchlist rows by finding leaf elements whose sibling is a pure digit
        // (the count badge next to each list name). This is unique to watchlist rows.
        const DIGIT_ONLY = /^\d+$/;
        const UI_STRINGS = new Set([
          "Watchlists", "My watchlists", "Hotlists", "Flagged lists",
          "Created lists", "Other", "Symbols", "Close menu", "Search",
        ]);

        const names = [];
        const seen = new Set();

        for (const el of document.querySelectorAll("*")) {
          if (el.childElementCount > 0) continue;
          const rect = el.getBoundingClientRect();
          if (rect.width === 0 || rect.height === 0) continue;
          const text = (el.textContent || "").trim().replace(/\s+/g, " ");
          if (!text || text.length > 50 || DIGIT_ONLY.test(text)) continue;
          if (UI_STRINGS.has(text)) continue;

          const parent = el.parentElement;
          if (!parent) continue;
          const hasCountBadge = Array.from(parent.children).some(
            (c) => c !== el && DIGIT_ONLY.test((c.textContent || "").trim())
          );
          if (!hasCountBadge) continue;

          const prect = parent.getBoundingClientRect();
          if (prect.width === 0 || prect.height === 0) continue;

          if (!seen.has(text)) { seen.add(text); names.push(text); }
        }

        return names;
      });

      console.log(`Found ${listNames.length} watchlist(s): ${listNames.join(", ")}`);
    }

    // Close whatever dialog/menu is open
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);
  }

  // If we found no list names, return current single list
  if (listNames.length === 0) {
    console.log("Could not enumerate lists — extracting current visible list.");
    const data = await extractSymbolsFromPage(page);
    return [{ name: currentListName, ...data }];
  }

  const allWatchlists = [];
  // Accumulate ticker → "EXCHANGE:SYMBOL" across all lists so that bare symbols
  // captured from later lists (where WS sends differential/no resend) can be
  // retroactively qualified once we have their exchange prefix from an earlier list.
  const globalTicker2Qualified = new Map();

  for (const name of listNames) {
    console.log(`Switching to: "${name}"`);

    // Re-open context menu → Open list... → click the name
    if (headerLocator) {
      await headerLocator.click();
      await page.waitForTimeout(800);
      for (const loc of [
        page.getByText("Open list…", { exact: true }),
        page.getByText("Open list...", { exact: true }),
        page.locator("text=Open list").first(),
      ]) {
        try {
          if (await loc.isVisible({ timeout: 1000 })) { await loc.click(); break; }
        } catch { /* skip */ }
      }
      await page.waitForTimeout(1500);
    }

    // Scroll the item into view in the dialog (handles off-screen items like Earnings/Seasonals/X),
    // then use page.mouse.click() for a trusted click that TradingView's React handlers accept.
    const coords = await page.evaluate((targetName) => {
      const DIGIT_ONLY = /^\d+$/;
      for (const el of document.querySelectorAll("*")) {
        if (el.childElementCount > 0) continue;
        const text = (el.textContent || "").trim();
        if (text !== targetName) continue;
        const parent = el.parentElement;
        if (!parent) continue;
        const hasCountBadge = Array.from(parent.children).some(
          (c) => c !== el && DIGIT_ONLY.test((c.textContent || "").trim())
        );
        if (!hasCountBadge) continue;
        parent.scrollIntoView({ block: "center", behavior: "instant" });
        const rect = parent.getBoundingClientRect();
        return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
      }
      return null;
    }, name);

    let clicked = false;
    if (coords) {
      // Tell the WS listener which list we're about to switch to
      activeListCapture = name;
      await page.waitForTimeout(200); // let scroll settle
      await page.mouse.click(coords.x, coords.y);
      clicked = true;
    }

    if (!clicked) {
      console.log(`  Could not click "${name}", skipping.`);
      await page.keyboard.press("Escape");
      // Wait for backdrop to clear before next iteration
      await page.locator("[class*='backdrop']").waitFor({ state: "hidden", timeout: 5000 }).catch(() => {});
      await page.waitForTimeout(500);
      continue;
    }

    // Wait for the picker dialog/backdrop to close after selection
    await page.locator("[class*='backdrop']").waitFor({ state: "hidden", timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(2500);

    // Open and close the watchlist header menu — triggers TradingView to re-emit
    // quote_add_symbols for the current list, which is needed when this list was
    // already active at page-load time (no subscription change = no initial WS event).
    if (headerLocator) {
      try {
        await headerLocator.click();
        await page.waitForTimeout(600);
        await page.keyboard.press("Escape");
        await page.waitForTimeout(400);
      } catch { /* ignore */ }
    }

    // DOM scroll: scrolling the watchlist triggers TradingView to subscribe (via WS) to more
    // symbols, so running both in parallel gives us the maximum symbol coverage.
    const domData = await extractAllSymbolsScrolling(page);

    // Wait a moment for any trailing WS messages triggered by the scroll
    await page.waitForTimeout(1000);

    // Merge WS symbols (clean EXCHANGE:SYMBOL format) with DOM symbols (broader coverage)
    const wsSyms = wsListSymbols.get(name);
    const merged = new Set(domData.symbols);
    if (wsSyms) {
      for (const s of wsSyms) {
        if (normalizeSymbol(s)) merged.add(s);
      }
    }
    // Remove bare symbols that are covered by a qualified EXCHANGE:SYMBOL from WS
    const wsTickers = new Set(wsSyms ? [...wsSyms].map((s) => s.split(":")[1]) : []);
    const symbols = [...merged].filter((s) => s.includes(":") || !wsTickers.has(s));

    const data = { count: symbols.length, symbols, sections: domData.sections };
    console.log(
      `  ${data.count} symbol(s) (DOM:${domData.count} WS:${wsSyms?.size ?? 0}).`
    );
    // Update global ticker→qualified map so subsequent lists can qualify their bare symbols
    for (const s of symbols) {
      if (s.includes(":")) globalTicker2Qualified.set(s.split(":")[1], s);
    }
    allWatchlists.push({ name, ...data });
  }

  // Retroactively qualify any bare symbols using the global map.
  // Bare symbols appear when a symbol was already subscribed in the WS session from a
  // previously processed list (differential subscription) and was only caught via DOM scroll.
  for (const watchlist of allWatchlists) {
    const qualified = watchlist.symbols.map((s) => {
      if (s.includes(":")) return s;
      return globalTicker2Qualified.get(s) ?? s;
    });
    // Deduplicate in case qualification produced a duplicate of an existing qualified entry
    const dedupedSet = new Set(qualified);
    // Remove bare versions that now have a qualified counterpart
    const deduped = [...dedupedSet].filter((s) => {
      if (s.includes(":")) return true;
      return !dedupedSet.has(globalTicker2Qualified.get(s) ?? "");
    });
    watchlist.symbols = deduped;
    watchlist.count = deduped.length;
  }

  return allWatchlists;
}

async function collectScreener(page) {
  await page.goto("https://www.tradingview.com/screener/", {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await page.waitForTimeout(7000);

  const rows = await page.evaluate(() => {
    const tableRows = Array.from(document.querySelectorAll("table tr"));
    return tableRows
      .map((row) => {
        const cells = Array.from(row.querySelectorAll("td, th"))
          .map((cell) => (cell.textContent || "").trim())
          .filter(Boolean);
        const hrefs = Array.from(row.querySelectorAll("a[href]")).map((a) => a.getAttribute("href") || "");
        return { cells, hrefs };
      })
      .filter((row) => row.cells.length > 0);
  });

  const normalizedRows = rows
    .map((row) => {
      const symbolFromHref = row.hrefs
        .map((href) => href.match(/\/symbols\/([^/?#]+)/)?.[1] ?? null)
        .find(Boolean);
      const symbolFromCell = row.cells.map(tickerFromText).find(Boolean);
      return {
        symbol: symbolFromHref || symbolFromCell || null,
        cells: row.cells,
      };
    })
    .filter((row) => row.symbol);

  return {
    source: "screener",
    url: page.url(),
    count: normalizedRows.length,
    rows: normalizedRows,
  };
}

// Convert /symbols/NASDAQ-AAOI/ href format to NASDAQ:AAOI
function symbolFromScreenerHref(href) {
  const m = href.match(/\/symbols\/([^/?#]+)/);
  if (!m) return null;
  // TradingView uses EXCHANGE-SYMBOL (dash separator) in URLs
  const raw = m[1].replace(/-/, ":");
  return normalizeSymbol(raw) || m[1];
}

// Extract all rows from the visible screener table, clicking "Show more" to paginate.
async function extractScreenerTable(page) {
  // Find the screener table (skip the first 1-2 empty tables TradingView injects)
  const headers = await page.evaluate(() => {
    for (const tbl of document.querySelectorAll("table")) {
      const thead = tbl.querySelector("thead tr");
      if (!thead) continue;
      const ths = Array.from(thead.querySelectorAll("th"))
        .map((c) => (c.textContent || "").trim())
        .filter(Boolean);
      if (ths.length > 2) return ths;
    }
    return [];
  });

  const allRows = new Map(); // key -> row data

  const collectVisible = async () => {
    const rows = await page.evaluate(() => {
      const result = [];
      for (const tbl of document.querySelectorAll("table")) {
        const bodyRows = tbl.querySelectorAll("tbody tr");
        if (bodyRows.length === 0) continue;
        // Skip empty placeholder tables
        const firstCells = Array.from(bodyRows[0].querySelectorAll("td")).map((c) => c.textContent.trim());
        if (firstCells.every((c) => !c)) continue;

        for (const row of bodyRows) {
          const cells = Array.from(row.querySelectorAll("td")).map((c) => (c.textContent || "").trim());
          if (cells.length < 2) continue;
          const href = row.querySelector("a[href*='/symbols/']")?.getAttribute("href") || "";
          const tickerEl = row.querySelector("[class*='tickerNameBox'], [class*='tickerName-']");
          const ticker = tickerEl ? tickerEl.textContent.trim() : null;
          result.push({ ticker, cells, href });
        }
        break; // found the real table
      }
      return result;
    });

    let added = 0;
    for (const row of rows) {
      const key = row.href || row.ticker || row.cells[0];
      if (!allRows.has(key)) {
        const symbol = symbolFromScreenerHref(row.href) || row.ticker || tickerFromText(row.cells[0]);
        allRows.set(key, { symbol, cells: row.cells });
        added++;
      }
    }
    return added;
  };

  await collectVisible();

  // Paginate via "Show more" button.
  // Must use Playwright's locator.click() (not JS .click()) since TradingView uses React
  // synthetic events that don't respond to programmatic DOM clicks.
  let rounds = 0;
  const maxRounds = 20; // safety cap (~1000 rows)
  while (rounds < maxRounds) {
    const showMoreBtn = page.locator("button").filter({ hasText: /^Show more$/ }).first();
    let btnVisible = false;
    try { btnVisible = await showMoreBtn.isVisible({ timeout: 1500 }); } catch { /* not found */ }
    if (!btnVisible) break;

    const before = allRows.size;
    await showMoreBtn.scrollIntoViewIfNeeded();
    await showMoreBtn.click({ timeout: 5000 });
    await page.waitForTimeout(2500);
    await collectVisible();
    if (allRows.size === before) break; // no new rows loaded
    rounds++;
  }

  return {
    headers,
    count: allRows.size,
    rows: [...allRows.values()],
  };
}

async function collectAllScreeners(page) {
  await page.goto("https://www.tradingview.com/screener/", {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await page.waitForTimeout(5000);

  // Get the current screener name
  let currentName = "";
  try {
    currentName = await page.locator("[class*='screenNameButton']").first().innerText();
    currentName = currentName.trim();
  } catch { /* ignore */ }
  console.log(`Current screener: "${currentName}"`);

  // Click the screener name button to open the saved-screeners dropdown
  const btnCoords = await page.evaluate(() => {
    const btn = document.querySelector("[class*='screenNameButton']");
    if (!btn) return null;
    const rect = btn.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  });
  if (!btnCoords) {
    console.log("Could not find screener name button — extracting current screener only.");
    const data = await extractScreenerTable(page);
    return [{ name: currentName || "Screener", ...data }];
  }

  await page.mouse.click(btnCoords.x, btnCoords.y);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUTPUT_DIR, "debug-screener-picker.png") });

  // Extract screener names from dropdown.
  // Key insight: dropdown items use [class*='background-'] as their container; table/watchlist
  // elements do NOT. This avoids false-positives from visible page content behind the dropdown.
  const screenerNames = await page.evaluate(() => {
    // Find "Recently used" label y (restrict to left half of page to avoid watchlist sidebar)
    const LEFT_MAX_X = 500;
    let recentlyUsedY = -1;
    let openScreenY = Infinity;

    for (const el of document.querySelectorAll("*")) {
      if (el.childElementCount > 0) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0 || rect.x > LEFT_MAX_X) continue;
      const text = (el.textContent || "").trim();
      if (text === "Recently used" && recentlyUsedY === -1) recentlyUsedY = rect.y;
      if ((text === "Open screen…" || text === "Open screen...") && openScreenY === Infinity) openScreenY = rect.y;
    }

    if (recentlyUsedY === -1) return [];

    // Collect background- items between recentlyUsedY and openScreenY.
    // background- class is only used by dropdown menu items (not table rows or watchlist items).
    const names = [];
    const seen = new Set();
    for (const el of document.querySelectorAll("[class*='background-']")) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0 || rect.x > LEFT_MAX_X) continue;
      if (rect.y <= recentlyUsedY || rect.y >= openScreenY) continue;
      const titleEl = el.querySelector("[class*='title-']");
      const text = titleEl ? titleEl.textContent.trim() : el.textContent.trim();
      if (!text || text.length < 2 || text.length > 100) continue;
      // Skip the "Open screen…" action item (its background- container sits just before openScreenY)
      if (text.startsWith("Open screen")) continue;
      if (!seen.has(text)) { seen.add(text); names.push(text); }
    }
    return names;
  });

  console.log(`Found ${screenerNames.length} screener(s): ${screenerNames.join(", ") || "(none)"}`);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);

  const names = screenerNames.length > 0 ? screenerNames : [currentName || "Screener"];
  const allScreeners = [];

  for (const name of names) {
    console.log(`\nExtracting screener: "${name}"`);

    // Switch to this screener (skip opening dropdown for the first/current one)
    const isCurrentAndFirst = name === currentName && allScreeners.length === 0;
    if (!isCurrentAndFirst) {
      // Re-open the dropdown
      const coords = await page.evaluate(() => {
        const btn = document.querySelector("[class*='screenNameButton']");
        if (!btn) return null;
        const rect = btn.getBoundingClientRect();
        return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
      });
      if (coords) {
        await page.mouse.click(coords.x, coords.y);
        await page.waitForTimeout(1200);
      }

      // Click the screener item by name — use background- container (dropdown items only)
      const itemCoords = await page.evaluate((targetName) => {
        const LEFT_MAX_X = 500;
        // Find "Recently used" y so we only look in the screener section of the dropdown
        let recentlyUsedY = -1;
        for (const el of document.querySelectorAll("*")) {
          if (el.childElementCount > 0) continue;
          const rect = el.getBoundingClientRect();
          if (rect.width === 0 || rect.height === 0 || rect.x > LEFT_MAX_X) continue;
          if ((el.textContent || "").trim() === "Recently used") { recentlyUsedY = rect.y; break; }
        }

        for (const el of document.querySelectorAll("[class*='background-']")) {
          const rect = el.getBoundingClientRect();
          if (rect.width === 0 || rect.height === 0 || rect.x > LEFT_MAX_X) continue;
          if (recentlyUsedY > -1 && rect.y <= recentlyUsedY) continue;
          const titleEl = el.querySelector("[class*='title-']");
          const text = titleEl ? titleEl.textContent.trim() : el.textContent.trim();
          if (text === targetName) {
            return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
          }
        }
        return null;
      }, name);

      if (!itemCoords) {
        console.log(`  Could not find "${name}" in dropdown, skipping.`);
        await page.keyboard.press("Escape");
        await page.waitForTimeout(300);
        continue;
      }
      await page.mouse.click(itemCoords.x, itemCoords.y);
      await page.waitForTimeout(3000); // wait for screener results to load
    }

    const data = await extractScreenerTable(page);
    console.log(`  ${data.count} row(s), columns: ${data.headers.join(", ")}`);
    allScreeners.push({ name, ...data });
  }

  return allScreeners;
}

async function saveJson(name, payload) {
  await ensureDir(OUTPUT_DIR);
  const file = path.join(OUTPUT_DIR, name);
  await fs.writeFile(file, JSON.stringify(payload, null, 2) + "\n", "utf8");
  return file;
}

async function scrape(mode) {
  const context = await launchBrowser();
  try {
    const page = await getPage(context);
    await page.goto("https://www.tradingview.com/", {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
    if (await isLikelyLoggedOut(page)) {
      throw new Error(
        "TradingView session is not logged in. Run `node scripts/tradingview_automation.mjs login` first."
      );
    }

    if (mode === "watchlist") {
      const data = await collectWatchlist(page);
      const file = await saveJson("watchlist.json", data);
      console.log(`Saved watchlist to ${file}`);
      console.log(`Symbols: ${data.symbols.slice(0, 30).join(", ")}`);
      return;
    }

    if (mode === "screener") {
      const data = await collectScreener(page);
      const file = await saveJson("screener.json", data);
      console.log(`Saved screener to ${file}`);
      console.log(`Rows: ${data.count}`);
      return;
    }

    if (mode === "all") {
      const watchlist = await collectWatchlist(page);
      const screener = await collectScreener(page);
      const watchlistFile = await saveJson("watchlist.json", watchlist);
      const screenerFile = await saveJson("screener.json", screener);
      console.log(`Saved watchlist to ${watchlistFile}`);
      console.log(`Saved screener to ${screenerFile}`);
      return;
    }

    if (mode === "allwatchlists") {
      const watchlists = await collectAllWatchlists(page);
      const screener = await collectScreener(page);
      const file = await saveJson("all-lists.json", { watchlists, screener });
      console.log(`Saved ${watchlists.length} watchlist(s) + screener to ${file}`);
      for (const wl of watchlists) {
        console.log(`  "${wl.name}": ${wl.count} symbol(s)`);
      }
      return;
    }

    if (mode === "allscreeners") {
      const screeners = await collectAllScreeners(page);
      const file = await saveJson("all-screens.json", { screeners });
      console.log(`\nSaved ${screeners.length} screener(s) to ${file}`);
      for (const s of screeners) {
        console.log(`  "${s.name}": ${s.count} row(s)`);
      }
      return;
    }

    throw new Error(`Unknown mode: ${mode}`);
  } finally {
    await closeContext(context);
  }
}

async function main() {
  await loadDotenv();

  if (command === "help") {
    console.log("Usage:");
    console.log("  node scripts/tradingview_automation.mjs login");
    console.log("  node scripts/tradingview_automation.mjs watchlist");
    console.log("  node scripts/tradingview_automation.mjs screener");
    console.log("  node scripts/tradingview_automation.mjs all");
    console.log("  node scripts/tradingview_automation.mjs allwatchlists");
  console.log("  node scripts/tradingview_automation.mjs allscreeners");
    console.log("");
    console.log("Optional environment:");
    console.log("  TRADINGVIEW_USE_APP_PROFILE=true");
    console.log("  TRADINGVIEW_APP_PROFILE_DIR=~/Library/Application Support/TradingView");
    process.exit(0);
  }

  if (command === "login") {
    await loginFlow();
    return;
  }

  await scrape(command);
}

export {
  normalizeSymbol,
  parseTvWsFrame,
  parseWatchlistFromLines,
  symbolFromScreenerHref,
  tickerFromText,
};

const isDirectRun =
  typeof process.argv[1] === "string" &&
  import.meta.url === pathToFileURL(process.argv[1]).href;

if (isDirectRun) {
  main().catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
