/*
 * IntelliPlan desktop — main process.
 *
 * This is a native application, not a bookmark to a website. The web app is
 * rendered in a BrowserWindow, but the things a student actually gains by
 * installing something live out here in the main process:
 *
 *   - OS notifications that fire whether or not a browser tab is open, and
 *     that click through to the right screen.
 *   - A tray icon that shows what is next without opening the window.
 *   - A global shortcut to start a study session from anywhere.
 *   - A real application menu, real window state, real offline handling.
 *   - A session that persists across restarts, so you sign in once.
 *
 * Security posture: the renderer runs with contextIsolation on, nodeIntegration
 * off, and sandbox on. It talks to the main process only through the narrow,
 * explicitly enumerated bridge in preload.js. Any navigation away from the
 * IntelliPlan origin is handed to the system browser rather than loaded here,
 * so a stray link cannot end up running inside the app's window.
 */

'use strict';

const {
  app, BrowserWindow, Tray, Menu, shell, Notification,
  globalShortcut, ipcMain, nativeImage, dialog, powerMonitor,
} = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const updater = require('./updater');

// ── Configuration ───────────────────────────────────────────────────

const DEFAULT_TARGET = 'https://intelliplan.tech';

/** Where the app points. Overridable for local development. */
function targetOrigin() {
  const raw = (process.env.IP_TARGET || readSetting('target') || DEFAULT_TARGET).trim();
  try {
    return new URL(raw).origin;
  } catch {
    return DEFAULT_TARGET;
  }
}

const POLL_INTERVAL_MS = 60_000;      // how often the tray refreshes "what's next"
const SHORTCUT_START_SESSION = 'CommandOrControl+Shift+S';

let mainWindow = null;
let tray = null;
let pollTimer = null;
let quitting = false;

// ── Tiny settings store ─────────────────────────────────────────────
// A few kilobytes of JSON in userData. Deliberately not a dependency:
// window bounds and a target URL do not justify one.

function settingsPath() {
  return path.join(app.getPath('userData'), 'settings.json');
}

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsPath(), 'utf8'));
  } catch {
    return {};
  }
}

function readSetting(key) {
  return readSettings()[key];
}

function writeSetting(key, value) {
  try {
    const next = { ...readSettings(), [key]: value };
    fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
    fs.writeFileSync(settingsPath(), JSON.stringify(next, null, 2));
  } catch (err) {
    console.warn('[settings] write failed:', err.message);
  }
}

// ── Window ──────────────────────────────────────────────────────────

function iconPath(name) {
  // The icons live in two places depending on how the app was started.
  //
  // Packaged, extraResources puts them next to app.asar, because build/ is
  // electron-builder's buildResources directory and is never copied into
  // the app itself. Looking only in build/ — as this did — silently found
  // nothing in every installed copy: the window fell back to Electron's
  // default icon and createTray() returned early, so the tray feature was
  // missing from the shipped app while working perfectly in development.
  //
  // Run from source there is no resourcesPath worth reading, so build/ is
  // still the answer. Check both rather than branching on app.isPackaged,
  // which is one more thing to get wrong.
  const candidates = [
    process.resourcesPath ? path.join(process.resourcesPath, name) : null,
    path.join(__dirname, '..', 'build', name),
  ];
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  return null;
}

function createWindow() {
  // Restore the size and position the student left it at. An app that
  // reopens as a default-sized rectangle in the middle of the screen every
  // time reads as a web page in a frame, which is what this is not.
  const bounds = readSetting('bounds') || {};
  const icon = iconPath('icon.png');

  mainWindow = new BrowserWindow({
    width: bounds.width || 1280,
    height: bounds.height || 860,
    x: bounds.x,
    y: bounds.y,
    minWidth: 380,
    minHeight: 560,
    show: false,
    title: 'IntelliPlan',
    backgroundColor: '#f5f4f1',
    autoHideMenuBar: process.platform !== 'darwin',
    ...(icon ? { icon } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: true,
    },
  });

  // Show only once painted. Electron's default is a white flash while the
  // page loads, which on a dark theme looks like a bug.
  mainWindow.once('ready-to-show', () => mainWindow.show());

  mainWindow.loadURL(targetOrigin(), { userAgent: userAgent() });

  mainWindow.on('close', (event) => {
    // Closing the window keeps the app alive in the tray — otherwise
    // notifications stop the moment the student tidies their desktop, which
    // defeats the point of installing it. Quit is explicit.
    if (!quitting && tray) {
      event.preventDefault();
      mainWindow.hide();
      return;
    }
    persistBounds();
  });

  mainWindow.on('resize', persistBounds);
  mainWindow.on('move', persistBounds);

  attachNavigationGuards(mainWindow);
  attachOfflineHandling(mainWindow);
}

function userAgent() {
  // Identify the desktop client so the server can tell it apart in logs and
  // can skip the "install our app" banners, which are meaningless here.
  return `${app.getName()}Desktop/${app.getVersion()} (${process.platform})`;
}

let boundsTimer = null;
function persistBounds() {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.isMinimized()) return;
  clearTimeout(boundsTimer);
  boundsTimer = setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      writeSetting('bounds', mainWindow.getNormalBounds());
    }
  }, 400);
}

/**
 * Keep the app's window on the app's own origin.
 *
 * Anything else — an OAuth provider, a link a student taps in their planner,
 * a Canvas page — opens in the system browser. Two reasons: a link should not
 * be able to navigate the application shell somewhere unexpected, and sign-in
 * flows for Google and friends actively refuse to run inside embedded
 * browser views.
 */
function attachNavigationGuards(win) {
  const origin = targetOrigin();

  const isInternal = (url) => {
    try {
      const parsed = new URL(url);
      if (parsed.origin === origin) return true;
      // Canvas finishes its OAuth in place and is happy to do so in an
      // embedded view. Google is not, and used to be allowed here: it
      // loaded accounts.google.com in this window, where Google's
      // embedded-browser check rejected it — fatally for the supervised
      // Family Link accounts that cannot fall back to anything else.
      // Google now goes out to the system browser via startGoogleSignIn().
      return /(^|\.)instructure\.com$/.test(parsed.hostname);
    } catch {
      return false;
    }
  };

  /**
   * Is this the app's own "Sign in with Google" link?
   *
   * Catching it by URL means the website's login page needs no knowledge of
   * the desktop client — the same button works in both, and a future change
   * to that template cannot silently reintroduce the embedded flow.
   */
  const isGoogleSignIn = (url) => {
    try {
      const parsed = new URL(url);
      return parsed.origin === origin && parsed.pathname === '/login/google';
    } catch {
      return false;
    }
  };

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isGoogleSignIn(url)) {
      startGoogleSignIn();
      return { action: 'deny' };
    }
    if (isInternal(url)) return { action: 'allow' };
    shell.openExternal(url);
    return { action: 'deny' };
  });

  win.webContents.on('will-navigate', (event, url) => {
    if (isGoogleSignIn(url)) {
      event.preventDefault();
      startGoogleSignIn();
      return;
    }
    if (isInternal(url)) return;
    event.preventDefault();
    shell.openExternal(url);
  });

  // Permission requests: grant only what the product genuinely uses, and
  // deny the rest outright rather than forwarding an OS prompt for something
  // the app never asked for.
  win.webContents.session.setPermissionRequestHandler((_wc, permission, callback) => {
    const allowed = ['notifications', 'media', 'clipboard-sanitized-write', 'fullscreen'];
    callback(allowed.includes(permission));
  });
}

/**
 * Offline handling — the last resort, not the first.
 *
 * The service worker registered by the web app runs inside this renderer
 * too, so a launch with no connection normally never reaches here: the
 * worker serves the cached page and the student sees their actual app.
 * Verified by killing the server and relaunching — the real UI comes up.
 *
 * `did-fail-load` therefore only fires when the worker has nothing to
 * serve, which in practice means a profile that has never once loaded
 * IntelliPlan online. That is why the card below says there is no offline
 * copy rather than reassuring the student their plan is safe: on the only
 * profile that sees it, nothing has ever been saved to the device.
 *
 * Without it, that case would show Chromium's dinosaur inside what is
 * supposed to be an application, with no way back.
 */
function attachOfflineHandling(win) {
  win.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (!isMainFrame || errorCode === -3 /* aborted, usually a redirect */) return;
    const html = offlinePage(errorDescription || 'Could not reach IntelliPlan', validatedURL);
    win.webContents.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
  });

  ipcMain.handle('app:retry', () => {
    win.loadURL(targetOrigin(), { userAgent: userAgent() });
  });
}

function offlinePage(reason, url) {
  const safe = String(reason).replace(/[<>&]/g, '');
  return `<!doctype html><html><head><meta charset="utf-8"><title>IntelliPlan — offline</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         font: 15px/1.6 -apple-system, "Segoe UI", Roboto, sans-serif;
         background:#f5f4f1; color:#1a1a1a; }
  @media (prefers-color-scheme: dark) { body { background:#101012; color:#e6e6e2; } }
  main { max-width: 34rem; padding: 2rem; text-align:center; }
  h1 { font-size: 1.4rem; font-weight: 600; margin: 0 0 .5rem; }
  p { margin: 0 0 1.5rem; opacity: .75; }
  button { font: inherit; font-weight:600; padding:.7rem 1.4rem; border-radius:999px;
           border:0; background:#1a56db; color:#fff; cursor:pointer; }
  code { opacity:.55; font-size:.8rem; }
</style></head><body><main>
  <h1>IntelliPlan can't reach the server</h1>
  <p>Nothing has been saved to this device yet, so there is no offline copy to
     show. Once you have opened IntelliPlan here with a connection, this window
     will keep working without one.</p>
  <button id="retry">Try again</button>
  <p><code>${safe}</code></p>
</main>
<script>
  document.getElementById('retry').addEventListener('click', () => window.intelliplan?.retry());
  window.addEventListener('online', () => window.intelliplan?.retry());
</script></body></html>`;
}

// ── Tray ────────────────────────────────────────────────────────────

function createTray() {
  const icon = iconPath('tray.png') || iconPath('icon.png');
  if (!icon) return;   // packaging without icons is not a reason to fail

  const image = nativeImage.createFromPath(icon).resize({ width: 18, height: 18 });
  image.setTemplateImage(true);      // macOS menu bar wants a template image
  tray = new Tray(image);
  tray.setToolTip('IntelliPlan');
  refreshTrayMenu(null);

  tray.on('click', showWindow);
}

//: The last "up next" the poll found. Remembered so the tray can be
//: rebuilt for an unrelated reason — an update finishing, say — without
//: blanking the line the student actually cares about.
let lastNext = null;

function refreshTrayMenu(next) {
  lastNext = next;
  if (!tray) return;
  const nextLabel = next
    ? `${next.title}${next.time_slot ? ` · ${next.time_slot}` : ''}`
    : 'Nothing scheduled';

  // A ready update earns a line in the tray. It is the one place a student
  // sees the app when the window is closed, which is exactly the state a
  // long-running install sits in for days.
  const update = updater.status();
  const updateItems = update.state === 'ready'
    ? [
        { label: `Update to ${update.version}`, click: () => updater.promptInstall() },
        { type: 'separator' },
      ]
    : [];

  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Up next', enabled: false },
    { label: nextLabel, enabled: false },
    { type: 'separator' },
    ...updateItems,
    { label: 'Start study session', accelerator: SHORTCUT_START_SESSION, click: openActive },
    { label: 'Open IntelliPlan', click: showWindow },
    { label: 'Scheduler', click: () => openPath('/scheduler') },
    { type: 'separator' },
    { label: 'Quit IntelliPlan', click: () => { quitting = true; app.quit(); } },
  ]));

  tray.setToolTip(next ? `IntelliPlan — ${nextLabel}` : 'IntelliPlan');
}

// ── Navigation helpers ──────────────────────────────────────────────

function showWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function openPath(pathname) {
  showWindow();
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadURL(targetOrigin() + pathname, { userAgent: userAgent() });
  }
}

function openActive() {
  openPath('/active');
}

// ── Google sign-in ──────────────────────────────────────────────────
//
// Google refuses to run OAuth inside an embedded browser view, and polices
// that hardest against supervised Family Link accounts — which is most of
// the students this app is for. So the sign-in page cannot be shown here.
// It goes to the real system browser, and the finished session comes back
// through the intelliplan:// handler as a one-time code.
//
// The code is only half a credential. The other half is the PKCE verifier
// below, which never leaves this process — any other local program can
// register intelliplan:// and read the code off the deep link, but without
// the verifier it cannot spend it. server-side rules live in
// desktop_auth.py.

/** The verifier for the sign-in currently in flight, if any. */
let pendingVerifier = null;

function b64url(raw) {
  return raw.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function startGoogleSignIn() {
  const verifier = b64url(crypto.randomBytes(32));
  const challenge = b64url(crypto.createHash('sha256').update(verifier).digest());
  // Replacing any previous attempt is deliberate: only the most recent
  // sign-in the student actually started should be redeemable.
  pendingVerifier = verifier;
  const url = `${targetOrigin()}/login/google?desktop=${encodeURIComponent(challenge)}`;
  shell.openExternal(url);
}

/**
 * Redeem the code the browser handed back.
 *
 * The exchange runs as a fetch inside the window rather than from the main
 * process, so the session cookie the server sets lands in the jar the app
 * actually browses with. Doing it here would mean copying cookies between
 * sessions, which is fiddly and a good way to leak a session into a log.
 */
async function completeGoogleSignIn(code) {
  const verifier = pendingVerifier;
  // Single use on this side too. A second deep link carrying a replayed
  // code finds nothing to spend it with.
  pendingVerifier = null;

  if (!verifier || !code) {
    showWindow();
    return;
  }
  if (!mainWindow || mainWindow.isDestroyed()) return;

  try {
    const ok = await mainWindow.webContents.executeJavaScript(
      `fetch('/api/desktop/auth/exchange', {
         method: 'POST',
         headers: { 'Content-Type': 'application/json' },
         credentials: 'same-origin',
         body: ${JSON.stringify(JSON.stringify({ code, verifier }))},
       }).then(r => r.ok).catch(() => false)`,
      true,
    );
    showWindow();
    if (ok) {
      mainWindow.loadURL(targetOrigin() + '/command-center', { userAgent: userAgent() });
    } else {
      // Expired or already spent. Sending them back to the login page is
      // more use than a dialog explaining PKCE.
      mainWindow.loadURL(targetOrigin() + '/login', { userAgent: userAgent() });
    }
  } catch {
    showWindow();
  }
}

// ── "What's next" polling ───────────────────────────────────────────
//
// Runs in the renderer's session so it carries the signed-in cookie, then
// hands the answer back here. Doing the fetch in the main process would need
// the cookie jar copied out, which is both fiddly and a good way to leak a
// session token into a log line.

async function pollNext() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  try {
    const result = await mainWindow.webContents.executeJavaScript(
      `fetch('/api/active/next', { credentials: 'same-origin' })
         .then(r => r.ok ? r.json() : null)
         .then(d => d && d.next ? JSON.stringify(d.next) : null)
         .catch(() => null)`,
      true,
    );
    refreshTrayMenu(result ? JSON.parse(result) : null);
  } catch {
    // A window that is mid-navigation cannot run script. Not worth logging
    // once a minute.
  }
}

/**
 * Tell the student an update is waiting.
 *
 * Deliberately a notification and not a dialog: the download finishes on
 * its own schedule, and a modal stealing focus mid-sentence to announce
 * good news is worse than the news is good. Clicking it opens the restart
 * prompt, where the actual decision lives.
 */
function notifyUpdateReady() {
  if (!Notification.isSupported()) return;
  const { version } = updater.status();
  const notification = new Notification({
    title: 'IntelliPlan update ready',
    body: version
      ? `Version ${version} will install when you restart.`
      : 'An update will install when you restart.',
    silent: true,
    ...(iconPath('icon.png') ? { icon: iconPath('icon.png') } : {}),
  });
  notification.on('click', () => updater.promptInstall());
  notification.show();
}

// ── Native notifications ────────────────────────────────────────────

ipcMain.handle('notify', (_event, payload) => {
  if (!Notification.isSupported()) return false;
  const { title, body, url } = payload || {};
  if (!title && !body) return false;

  const notification = new Notification({
    title: String(title || 'IntelliPlan').slice(0, 120),
    body: String(body || '').slice(0, 400),
    silent: false,
    ...(iconPath('icon.png') ? { icon: iconPath('icon.png') } : {}),
  });

  notification.on('click', () => {
    // A notification you cannot act on is an interruption. Clicking one
    // lands on the screen it is about.
    if (url && typeof url === 'string' && url.startsWith('/')) openPath(url);
    else showWindow();
  });

  notification.show();
  return true;
});


/* ── System volume ────────────────────────────────────────────────────
 *
 * A web page cannot change the operating system's volume, and no browser
 * is going to let it. That is the right call almost everywhere — and it
 * is exactly wrong for a study alarm the student explicitly asked to be
 * unmissable, which is useless if they turned the volume down before
 * getting distracted.
 *
 * The desktop build can do it properly, so it does. Each platform gets
 * the mechanism it actually has:
 *
 *   macOS    osascript, which is present on every install
 *   Linux    pactl or amixer, whichever the box has
 *   Windows  PowerShell driving the keyboard volume-up key
 *
 * Windows deserves a note. There is no supported command-line volume API
 * without shipping a native module or a binary like nircmd, and adding a
 * compiled dependency to a study planner for one feature is a bad trade.
 * Sending volume-up keystrokes reaches the same place: each press is
 * ~2% on a default mixer, so fifty presses saturate it from any starting
 * point. It is inelegant and it works with nothing installed.
 *
 * Everything here is fire-and-forget. A student whose machine has no
 * mixer we recognise still gets the in-page alarm at full gain — the
 * volume raise is an enhancement, never a prerequisite.
 */
const { execFile } = require('child_process');

/** Longest any volume command may run before it is abandoned. */
const VOLUME_TIMEOUT_MS = 4000;

function runVolumeCommand(cmd, args) {
  return new Promise((resolve) => {
    try {
      const child = execFile(cmd, args, { timeout: VOLUME_TIMEOUT_MS }, (err) => {
        resolve(!err);
      });
      child.on('error', () => resolve(false));
    } catch (e) {
      resolve(false);
    }
  });
}

async function setSystemVolume(level) {
  // 0..1, clamped. Anything outside that is a caller bug, not an instruction.
  const value = Math.max(0, Math.min(1, Number(level) || 0));

  if (process.platform === 'darwin') {
    // 0-100 scale, and unmute: a muted machine ignores the level entirely.
    const pct = Math.round(value * 100);
    return runVolumeCommand('osascript', [
      '-e', `set volume output volume ${pct}`,
      '-e', 'set volume without output muted',
    ]);
  }

  if (process.platform === 'linux') {
    const pct = Math.round(value * 100);
    if (await runVolumeCommand('pactl', ['set-sink-mute', '@DEFAULT_SINK@', '0'])) {
      if (await runVolumeCommand('pactl', ['set-sink-volume', '@DEFAULT_SINK@', `${pct}%`])) {
        return true;
      }
    }
    await runVolumeCommand('amixer', ['-q', 'sset', 'Master', 'unmute']);
    return runVolumeCommand('amixer', ['-q', 'sset', 'Master', `${pct}%`]);
  }

  if (process.platform === 'win32') {
    // 0xAF = volume up, 0xAD = mute toggle. Unmute first by pressing the
    // mute key only if we are raising the volume — pressing it blind would
    // mute an already-unmuted machine, which is the opposite of the point.
    // Raising from a muted state is handled by the volume-up presses
    // themselves, which unmute on Windows.
    const presses = Math.ceil(value * 50);
    const script =
      '$w = New-Object -ComObject WScript.Shell; ' +
      `1..${presses} | ForEach-Object { $w.SendKeys([char]175) }`;
    return runVolumeCommand('powershell', [
      '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
      '-Command', script,
    ]);
  }

  return false;
}

ipcMain.handle('system:setVolume', async (_event, level) => {
  try {
    return await setSystemVolume(level);
  } catch (e) {
    // Never let a mixer quirk propagate into the renderer as a rejection.
    return false;
  }
});


ipcMain.handle('app:info', () => ({
  platform: process.platform,
  version: app.getVersion(),
  isDesktop: true,
}));

ipcMain.handle('app:setTarget', (_event, url) => {
  try {
    const origin = new URL(url).origin;
    writeSetting('target', origin);
    return origin;
  } catch {
    return null;
  }
});

// ── Application menu ────────────────────────────────────────────────

function buildMenu() {
  const isMac = process.platform === 'darwin';
  const updateStatus = updater.status();
  const template = [
    ...(isMac ? [{ role: 'appMenu' }] : []),
    {
      label: 'File',
      submenu: [
        { label: 'Start Study Session', accelerator: SHORTCUT_START_SESSION, click: openActive },
        { type: 'separator' },
        isMac ? { role: 'close' } : { role: 'quit' },
      ],
    },
    {
      label: 'Go',
      submenu: [
        { label: 'Today', accelerator: 'CmdOrCtrl+1', click: () => openPath('/command-center') },
        { label: 'Scheduler', accelerator: 'CmdOrCtrl+2', click: () => openPath('/scheduler') },
        { label: 'Active', accelerator: 'CmdOrCtrl+3', click: openActive },
        { label: 'Dashboard', accelerator: 'CmdOrCtrl+4', click: () => openPath('/dashboard') },
        { type: 'separator' },
        { label: 'Back', accelerator: 'CmdOrCtrl+[', click: () => mainWindow?.webContents.navigationHistory.canGoBack() && mainWindow.webContents.navigationHistory.goBack() },
        { label: 'Reload', accelerator: 'CmdOrCtrl+R', click: () => mainWindow?.reload() },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        { type: 'separator' }, { role: 'togglefullscreen' },
        { type: 'separator' }, { role: 'toggleDevTools' },
      ],
    },
    {
      role: 'help',
      submenu: [
        // Label follows the updater, so the menu never offers a check when
        // one has already finished and is sitting there waiting.
        updateStatus.state === 'ready'
          ? { label: `Restart to Update to ${updateStatus.version}`, click: () => updater.promptInstall() }
          : { label: 'Check for Updates…', click: () => updater.check({ interactive: true }) },
        { type: 'separator' },
        { label: 'IntelliPlan Help', click: () => shell.openExternal(targetOrigin() + '/faq') },
        { label: 'Report a Problem', click: () => shell.openExternal(targetOrigin() + '/contact') },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ── Lifecycle ───────────────────────────────────────────────────────

// One instance only. A second launch focuses the running window instead of
// opening a duplicate that competes for the same session and tray icon.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', (_event, argv) => {
    showWindow();
    const deepLink = argv.find((a) => a.startsWith('intelliplan://'));
    if (deepLink) handleDeepLink(deepLink);
  });

  app.whenReady().then(() => {
    // Claim intelliplan:// ourselves rather than trusting the installer to
    // have done it. Google sign-in now depends on this handler existing —
    // the browser finishes the round-trip and hands the result back through
    // it — so a copy that was moved, unpacked by hand, or installed by an
    // installer that half-failed would otherwise have no way to complete a
    // login. Cheap to repeat, and it makes `npm start` behave like the
    // packaged app.
    if (!app.isDefaultProtocolClient('intelliplan')) {
      app.setAsDefaultProtocolClient('intelliplan');
    }

    createWindow();
    createTray();
    buildMenu();

    globalShortcut.register(SHORTCUT_START_SESSION, openActive);

    // Rebuild the surfaces that show update state whenever it moves, so
    // "Update to 1.0.1" appears in the tray and menu the moment a download
    // finishes — rather than at the next restart, which for a tray-resident
    // app could be weeks away.
    updater.start({
      notify: (state) => {
        buildMenu();
        refreshTrayMenu(lastNext);
        if (state === 'ready') notifyUpdateReady();
      },
    });

    pollTimer = setInterval(pollNext, POLL_INTERVAL_MS);
    setTimeout(pollNext, 8_000);   // after the first page has had time to load

    // A laptop that wakes from sleep has a stale plan on screen. Refresh the
    // tray so "up next" is not yesterday's answer.
    powerMonitor.on('resume', () => setTimeout(pollNext, 3_000));

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
      else showWindow();
    });
  });

  app.on('before-quit', () => { quitting = true; });

  app.on('will-quit', () => {
    globalShortcut.unregisterAll();
    clearInterval(pollTimer);
    updater.stop();
  });

  app.on('window-all-closed', () => {
    // The tray keeps the app running deliberately; without a tray there is
    // nothing left to interact with, so quitting is correct.
    if (!tray && process.platform !== 'darwin') app.quit();
  });

  app.on('open-url', (event, url) => {      // macOS deep links
    event.preventDefault();
    handleDeepLink(url);
  });
}

function handleDeepLink(url) {
  try {
    const parsed = new URL(url);
    // intelliplan://auth?code=… is the browser handing back a finished
    // Google sign-in. It is not a page to open — the host carries the
    // meaning here, because a custom scheme puts "auth" in hostname rather
    // than pathname.
    if (parsed.hostname === 'auth') {
      completeGoogleSignIn(parsed.searchParams.get('code'));
      return;
    }
    const target = parsed.pathname && parsed.pathname !== '/' ? parsed.pathname : '/active';
    openPath(target);
  } catch {
    showWindow();
  }
}

// Never let an unexpected error take the whole app down silently.
process.on('uncaughtException', (err) => {
  console.error('[main] uncaught:', err);
  if (app.isReady()) {
    dialog.showErrorBox('IntelliPlan', 'Something went wrong. The window will reload.');
    mainWindow?.reload();
  }
});
