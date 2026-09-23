/* Renders the daily reel from a content file.
   node render-video.js <content.json> <out-dir>             → reel.mp4 + reel-sfx-only.mp4
   node render-video.js <content.json> <out-dir> --probe 1.5,6,9.8   → probe-*.jpg stills

   Frames are rendered one at a time from reel.html's seek(t), so motion is
   exact at any frame rate; the soundtrack comes from synth.js on the same
   fixed timeline. Needs setup.sh to have run (fonts + ffmpeg). */
const path = require('path'), fs = require('fs'), os = require('os');
const { spawn, execFileSync } = require('child_process');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || '/opt/node22/lib/node_modules/playwright');

const HERE = __dirname;
const [contentPath, outDir, flag, probeArg] = process.argv.slice(2);
if (!contentPath || !outDir) { console.error('usage: node render-video.js <content.json> <out-dir> [--probe t1,t2]'); process.exit(2); }
const FPS = Number(process.env.FPS || 60);
const CHROME = process.env.CHROME_PATH || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';

function ffmpegPath() {
  if (process.env.FFMPEG) return process.env.FFMPEG;
  try { return require(require.resolve('ffmpeg-static', { paths: [path.join(os.homedir(), '.cache/daily-content')] })); }
  catch { throw new Error('ffmpeg-static not found — run setup.sh'); }
}

(async () => {
  const content = JSON.parse(fs.readFileSync(contentPath, 'utf8'));
  if (!content.hook || !Array.isArray(content.tips) || content.tips.length !== 7 || !content.cta)
    throw new Error('content.json needs hook, cta and exactly 7 tips');
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await chromium.launch({ executablePath: fs.existsSync(CHROME) ? CHROME : undefined,
    args: ['--force-device-scale-factor=1', '--hide-scrollbars', '--font-render-hinting=none', '--no-sandbox'] });
  const pg = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
  const errors = []; pg.on('pageerror', e => errors.push(e.message));
  await pg.goto('file://' + path.join(HERE, 'reel.html'), { waitUntil: 'load' });
  await pg.waitForFunction(() => window.__ready === true);
  await pg.evaluate(() => document.fonts.ready);
  const missing = await pg.evaluate(() => ['Bricolage Grotesque', 'Instrument Sans', 'JetBrains Mono']
    .filter(f => !document.fonts.check(`700 40px "${f}"`)));
  if (missing.length) throw new Error('fonts not installed: ' + missing.join(', ') + ' — run setup.sh');
  await pg.evaluate(c => window.__setContent(c), content);
  await pg.waitForTimeout(200);
  const DUR = await pg.evaluate(() => window.DUR);

  if (flag === '--probe') {
    for (const t of (probeArg || '2,6,9.9,34.8').split(',').map(Number)) {
      await pg.evaluate(x => window.seek(x), t);
      await pg.screenshot({ path: path.join(outDir, `probe-${String(t).replace('.', '_')}.jpg`), type: 'jpeg', quality: 85 });
    }
    await browser.close();
    if (errors.length) { console.error('PAGE ERRORS:\n' + errors.join('\n')); process.exit(1); }
    return console.log('probed →', outDir);
  }

  const FF = ffmpegPath();
  const silent = path.join(outDir, '.silent.mp4');
  const ff = spawn(FF, ['-y', '-f', 'image2pipe', '-framerate', String(FPS), '-i', 'pipe:0',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-pix_fmt', 'yuv420p', '-profile:v', 'high',
    '-movflags', '+faststart', '-r', String(FPS), silent], { stdio: ['pipe', 'ignore', 'inherit'] });
  const write = b => new Promise(r => ff.stdin.write(b) ? r() : ff.stdin.once('drain', r));
  const total = Math.round(DUR * FPS), t0 = Date.now();
  for (let f = 0; f < total; f++) {
    await pg.evaluate(x => window.seek(x), f / FPS);
    await write(await pg.screenshot({ type: 'jpeg', quality: 95 }));
    if (f % 600 === 0) console.log(`frame ${f}/${total}  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  }
  ff.stdin.end(); await new Promise(r => ff.on('close', r));
  await browser.close();
  if (errors.length) { console.error('PAGE ERRORS:\n' + errors.join('\n')); process.exit(1); }

  /* soundtrack, then mux two versions */
  execFileSync(process.execPath, [path.join(HERE, 'synth.js')], { cwd: outDir, stdio: 'inherit' });
  const mux = (wav, out) => execFileSync(FF, ['-v', 'error', '-y', '-i', silent, '-i', path.join(outDir, wav),
    '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k', '-shortest',
    '-movflags', '+faststart', path.join(outDir, out)]);
  mux('full.wav', 'reel.mp4');
  mux('sfx.wav', 'reel-sfx-only.mp4');
  ['.silent.mp4', 'full.wav', 'sfx.wav'].forEach(f => fs.rmSync(path.join(outDir, f), { force: true }));
  console.log(`rendered reel.mp4 + reel-sfx-only.mp4 (${DUR}s, ${FPS}fps) → ${outDir}`);
})().catch(e => { console.error(e.message); process.exit(1); });
