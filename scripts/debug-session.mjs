import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');
const PROFILE_DIR = path.join(ROOT, '.tradingview-profile');

const envContent = fs.readFileSync(path.join(ROOT, '.env'), 'utf8');
const match = envContent.match(/^TRADINGVIEW_CHROME_PATH=(.+)$/m);
const chromePath = match ? match[1].trim().replace(/^["']|["']$/g, '') : undefined;

const context = await chromium.launchPersistentContext(PROFILE_DIR, {
  executablePath: chromePath,
  headless: true,
  args: ['--no-sandbox'],
});

const page = await context.newPage();
await page.goto('https://www.tradingview.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });

const body = (await page.locator('body').innerText()).toLowerCase();
console.log('--- Body snippet (first 800 chars) ---');
console.log(body.substring(0, 800));
console.log('---');
console.log('Has "get started":', body.includes('get started'));
console.log('Has "sign in":', body.includes('sign in'));

const cookies = await context.cookies('https://www.tradingview.com');
console.log('Active cookies:', cookies.map(c => c.name).join(', '));

await context.close();
