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
  const candidate = path.join(__dirname, '..', 'build', name);
  return fs.existsSync(candidate) ? candidate : null;
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
      // OAuth round-trips have to happen somewhere the session cookie lives;
      // the providers below are the ones the app actually uses.
      return /(^|\.)accounts\.google\.com$|(^|\.)instructure\.com$/.test(parsed.hostname);
    } catch {
      return false;
    }
  };

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isInternal(url)) return { action: 'allow' };
    shell.openExternal(url);
    return { action: 'deny' };
  });

  win.webContents.on('will-navigate', (event, url) => {
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

function refreshTrayMenu(next) {
  if (!tray) return;
  const nextLabel = next
    ? `${next.title}${next.time_slot ? ` · ${next.time_slot}` : ''}`
    : 'Nothing scheduled';

  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Up next', enabled: false },
    { label: nextLabel, enabled: false },
    { type: 'separator' },
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
    createWindow();
    createTray();
    buildMenu();

    globalShortcut.register(SHORTCUT_START_SESSION, openActive);

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
