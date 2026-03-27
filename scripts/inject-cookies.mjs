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

console.log('Profile dir:', PROFILE_DIR);
console.log('Chrome path:', chromePath);

const cookies = [
  { name: 'sessionid', value: 'l5e6azal33lbjezln7pi2ukctcsc4tat', domain: '.tradingview.com', path: '/', secure: true, httpOnly: true, sameSite: 'Lax' },
  { name: 'sessionid_sign', value: 'v3:qpoO1y+vNre8jaJcq462KqtgXSAgnv1sWrWB8+wLIio=', domain: '.tradingview.com', path: '/', secure: true, httpOnly: true, sameSite: 'Lax' },
];

const context = await chromium.launchPersistentContext(PROFILE_DIR, {
  executablePath: chromePath,
  headless: true,
  args: ['--no-sandbox'],
});

await context.addCookies(cookies);
const saved = await context.cookies('https://www.tradingview.com');
console.log('sessionid saved:', !!saved.find(c => c.name === 'sessionid'));
await context.close();
console.log('Done.');
