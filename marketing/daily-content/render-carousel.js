/* Renders a carousel page to ten 1080×1350 PNGs plus a preview strip.
   node render-carousel.js <carousel.html> <out-dir>

   The page must lay its slides out on a 10800px track and expose
   window.showSlide(i) and window.__ready — reference.html does both. */
const path = require('path'), fs = require('fs');
/* Find Playwright and Chromium wherever this machine keeps them, so an
   image update that moves either one doesn't break the daily run. */
function findPlaywright() {
  const tries = [process.env.PLAYWRIGHT_PATH, 'playwright'];
  try { tries.push(require('path').join(require('child_process').execSync('npm root -g').toString().trim(), 'playwright')); } catch {}
  tries.push('/opt/node22/lib/node_modules/playwright');
  for (const t of tries.filter(Boolean)) { try { return require(t); } catch {} }
  throw new Error('Playwright not found — set PLAYWRIGHT_PATH');
}
function findChrome() {
  const fs = require('fs'), path = require('path');
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try {
    const dirs = fs.readdirSync(root).filter(d => /^chromium-\d+$/.test(d)).sort((a, b) => b.split('-')[1] - a.split('-')[1]);
    for (const d of dirs) { const c = path.join(root, d, 'chrome-linux', 'chrome'); if (fs.existsSync(c)) return c; }
  } catch {}
  return undefined;                                     // let Playwright use its own default
}
const { chromium } = findPlaywright();

const [page, outDir] = process.argv.slice(2);
if (!page || !outDir) { console.error('usage: node render-carousel.js <carousel.html> <out-dir>'); process.exit(2); }

(async () => {
  const browser = await chromium.launch({ executablePath: findChrome(),
    args: ['--force-device-scale-factor=1', '--hide-scrollbars', '--font-render-hinting=none', '--no-sandbox'] });
  const pg = await browser.newPage({ viewport: { width: 1080, height: 1350 }, deviceScaleFactor: 1 });
  const errors = [];
  pg.on('pageerror', e => errors.push(e.message));
  await pg.goto('file://' + path.resolve(page), { waitUntil: 'load' });
  await pg.waitForFunction(() => window.__ready === true);
  await pg.evaluate(() => document.fonts.ready);
  await pg.waitForTimeout(300);

  /* A missing face falls back without error, which is the one failure you
     cannot see in a thumbnail — so it fails the render instead. */
  const missing = await pg.evaluate(() => ['Bricolage Grotesque', 'Instrument Sans', 'JetBrains Mono']
    .filter(f => !document.fonts.check(`700 40px "${f}"`)));
  if (missing.length) throw new Error('fonts not installed: ' + missing.join(', ') + ' — run setup.sh');

  fs.mkdirSync(outDir, { recursive: true });
  const shots = [];
  for (let i = 0; i < 10; i++) {
    await pg.evaluate(x => window.showSlide(x), i);
    const f = path.join(outDir, `slide-${String(i + 1).padStart(2, '0')}.png`);
    await pg.screenshot({ path: f, type: 'png' });
    shots.push(f);
  }
  /* preview strip: two rows of five, built from the PNGs just written */
  const imgs = shots.map(f => 'data:image/png;base64,' + fs.readFileSync(f).toString('base64'));
  await pg.setViewportSize({ width: 2160, height: 1080 });
  await pg.setContent(`<body style="margin:0;display:grid;grid-template-columns:repeat(5,432px);background:#000">${
    imgs.map(s => `<img src="${s}" style="width:432px;height:540px;display:block">`).join('')}</body>`);
  await pg.waitForTimeout(200);
  await pg.screenshot({ path: path.join(outDir, 'preview.jpg'), type: 'jpeg', quality: 85 });

  await browser.close();
  if (errors.length) { console.error('PAGE ERRORS:\n' + errors.join('\n')); process.exit(1); }
  console.log(`rendered ${shots.length} slides + preview → ${outDir}`);
})().catch(e => { console.error(e.message); process.exit(1); });
