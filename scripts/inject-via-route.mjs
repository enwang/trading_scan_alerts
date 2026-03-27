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

await fs.promises.rm(PROFILE_DIR, { recursive: true, force: true });
await fs.promises.mkdir(PROFILE_DIR, { recursive: true });
console.log('Fresh profile created.');

const context = await chromium.launchPersistentContext(PROFILE_DIR, {
  executablePath: chromePath,
  headless: true,
  args: ['--no-sandbox'],
});

// Intercept TradingView homepage and inject Set-Cookie headers into the response
await context.route('https://www.tradingview.com/', async (route) => {
  const response = await route.fetch();
  const headers = response.headers();
  const setCookies = [
    'sessionid=l5e6azal33lbjezln7pi2ukctcsc4tat; Domain=.tradingview.com; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=31536000',
    'sessionid_sign=v3:qpoO1y+vNre8jaJcq462KqtgXSAgnv1sWrWB8+wLIio=; Domain=.tradingview.com; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=31536000',
  ];
  const existing = headers['set-cookie'];
  headers['set-cookie'] = existing ? existing + '\n' + setCookies.join('\n') : setCookies.join('\n');
  console.log('Injecting Set-Cookie into response...');
  await route.fulfill({ response, headers });
});

const page = await context.newPage();
// First pass: response injects Set-Cookie so Chrome saves sessionid/sessionid_sign
await page.goto('https://www.tradingview.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
console.log('First pass done.');

// Second pass: navigate again — this time sessionid cookies are in the jar
await page.reload({ waitUntil: 'domcontentloaded', timeout: 60000 });
const body = (await page.locator('body').innerText()).toLowerCase();
console.log('Logged out:', body.includes('get started') && body.includes('sign in'));

const cookies = await context.cookies('https://www.tradingview.com');
console.log('Active cookies:', cookies.map(c => c.name).join(', '));

await context.close();
console.log('Done - profile saved.');
