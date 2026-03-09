#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";

const ROOT = process.cwd();
const scriptPath = path.join(ROOT, "scripts", "tradingview_ui.applescript");
const command = process.argv[2] || "help";
const args = process.argv.slice(3);

function runAppleScript(extraArgs) {
  const result = spawnSync("osascript", [scriptPath, ...extraArgs], {
    encoding: "utf8",
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    const message = (result.stderr || result.stdout || "osascript failed").trim();
    throw new Error(message);
  }
  return (result.stdout || "").trim();
}

function printHelp() {
  console.log("Usage:");
  console.log("  node scripts/tradingview_ui.mjs activate");
  console.log("  node scripts/tradingview_ui.mjs screenshot /tmp/tradingview.png");
  console.log("  node scripts/tradingview_ui.mjs keycode 125");
  console.log("  node scripts/tradingview_ui.mjs keycode 126");
  console.log("  node scripts/tradingview_ui.mjs keycode 3 command");
  console.log("  node scripts/tradingview_ui.mjs keystroke AAPL");
  console.log("  node scripts/tradingview_ui.mjs scroll -8");
  console.log("  node scripts/tradingview_ui.mjs next_row");
  console.log("  node scripts/tradingview_ui.mjs prev_row");
  console.log("  node scripts/tradingview_ui.mjs page_down");
  console.log("  node scripts/tradingview_ui.mjs page_up");
  console.log("  node scripts/tradingview_ui.mjs open_symbol_search");
  console.log("  node scripts/tradingview_ui.mjs window_title");
}

try {
  if (command === "help") {
    printHelp();
    process.exit(0);
  }

  const output = runAppleScript([command, ...args]);
  if (output) {
    console.log(output);
  }
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
