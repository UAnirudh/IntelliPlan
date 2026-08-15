'use strict';

/**
 * Keeping the desktop app current.
 *
 * The web app updates the moment a student reloads. The desktop app does
 * not, so without this they stay on whatever build they first installed —
 * indefinitely, and with no way to know they are behind.
 *
 * Shape of the thing, and why:
 *
 * - **Nothing installs without being asked.** The download happens quietly
 *   in the background, but applying it restarts the app, and restarting an
 *   app someone is working in is not a decision to make for them. So the
 *   update waits at "ready" — visible in the tray and the menu — until
 *   they say go.
 * - **Failure is silent.** No network, a school firewall, a rate-limited
 *   GitHub: none of that is a student's problem to see. An update that
 *   cannot be found should look exactly like no update.
 * - **It only runs where it can work.** Unpackaged there is nothing to
 *   replace. On an unsigned macOS build Squirrel.Mac rejects the update at
 *   the signature check. The Windows ARM64 build ships as a zip with no
 *   installer behind it. In all three cases offering an update would only
 *   produce an error nobody can act on.
 *
 * The feed is the GitHub releases the desktop workflow publishes; the
 * publish block in package.json points at it, and electron-builder writes
 * app-update.yml into the package from that same config.
 */

const { app, dialog, shell } = require('electron');

//: First check runs after the window has settled. A student opening the
//: app wants their plan, not a network round-trip competing with it.
const FIRST_CHECK_DELAY_MS = 25_000;

//: Re-check for long-running installs. This app is often left in the tray
//: for days, which is precisely the case that otherwise never updates.
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

let autoUpdater = null;
let timer = null;
let state = 'idle';        // idle | checking | downloading | ready | unsupported
let readyVersion = null;
let onStateChange = () => {};

/**
 * Can this build update itself at all?
 *
 * False here is not a failure — it is the honest answer for the three
 * cases below, and it keeps the UI from offering something that cannot
 * happen.
 */
function updatesSupported() {
  if (!app.isPackaged) return false;              // nothing to replace
  // macOS refuses unsigned updates at the Squirrel signature check. Once a
  // Developer ID is configured this can widen; until then, offering it
  // produces an error the student cannot act on.
  if (process.platform === 'darwin' && !process.env.IP_MAC_SIGNED) return false;
  // The Windows ARM64 build is a zip, with no installer to hand off to.
  if (process.platform === 'win32' && process.arch === 'arm64') return false;
  return true;
}

/**
 * Optional file log for the updater.
 *
 * electron-updater fails quietly in a lot of places — a rejected download
 * looks identical to no update at all from the outside. Without somewhere
 * to look, "it did not update" is unanswerable.
 */
function updaterLogger() {
  const target = process.env.IP_UPDATER_LOG;
  if (!target) return null;
  const fs = require('node:fs');
  const write = (level) => (...args) => {
    const line = `[${level}] ${args.map(a =>
      a && a.stack ? a.stack : (typeof a === 'object' ? JSON.stringify(a) : String(a))
    ).join(' ')}\n`;
    try { fs.appendFileSync(target, line); } catch { /* logging must not throw */ }
  };
  return { info: write('info'), warn: write('warn'), error: write('error'), debug: write('debug') };
}

function setState(next, version) {
  state = next;
  if (version) readyVersion = version;
  try { onStateChange(state, readyVersion); } catch { /* UI is best-effort */ }
}

/** What the tray and menu should say right now. */
function status() {
  return { state, version: readyVersion, supported: updatesSupported() };
}

/**
 * Apply a downloaded update and restart.
 *
 * Only ever reached from a control the student clicked. quitAndInstall
 * tears the app down, so anything that must survive has to have been
 * persisted already — window bounds are written on move/resize rather than
 * on quit for exactly this reason.
 */
function install() {
  if (state !== 'ready' || !autoUpdater) return false;
  setImmediate(() => autoUpdater.quitAndInstall(false, true));
  return true;
}

/**
 * Ask whether anything is newer.
 *
 * `interactive` separates the background poll from a student choosing
 * "Check for Updates…". The background one says nothing when there is
 * nothing; the explicit one owes an answer either way, because silence
 * after clicking a button reads as broken.
 */
async function check({ interactive = false } = {}) {
  if (!updatesSupported()) {
    if (interactive) {
      const isArmWindows = process.platform === 'win32' && process.arch === 'arm64';
      const { response } = await dialog.showMessageBox({
        type: 'info',
        message: 'This build does not update itself',
        detail: isArmWindows
          ? 'The Windows ARM64 build ships as a zip rather than an installer, so it has to be replaced by hand. The download page always has the current version.'
          : 'Automatic updates are not available for this build. The download page always has the current version.',
        buttons: ['Open Download Page', 'OK'],
        defaultId: 1,
        cancelId: 1,
      });
      if (response === 0) shell.openExternal('https://intelliplan.tech/download');
    }
    return;
  }

  if (!autoUpdater) return;

  // Already downloaded and waiting: re-checking would find the same thing.
  if (state === 'ready') {
    if (interactive) await promptInstall();
    return;
  }

  try {
    setState('checking');
    const result = await autoUpdater.checkForUpdates();
    const found = result && result.updateInfo &&
                  result.updateInfo.version !== app.getVersion();
    if (!found) {
      setState('idle');
      if (interactive) {
        await dialog.showMessageBox({
          type: 'info',
          message: `IntelliPlan ${app.getVersion()} is up to date.`,
          buttons: ['OK'],
        });
      }
    }
    // When there IS one, the download events below drive the rest.
  } catch (err) {
    // Offline, rate-limited, no release published yet. All the same to a
    // student, and none of it worth interrupting them for.
    setState('idle');
    console.warn('[updater] check failed:', err && err.message);
    if (interactive) {
      await dialog.showMessageBox({
        type: 'info',
        message: 'Could not check for updates',
        detail: 'IntelliPlan could not reach the update server. It will try again later.',
        buttons: ['OK'],
      });
    }
  }
}

/** The one place that asks to restart. */
async function promptInstall() {
  const { response } = await dialog.showMessageBox({
    type: 'question',
    message: `IntelliPlan ${readyVersion || ''} is ready to install`.replace('  ', ' '),
    detail: 'The app will restart to finish updating. Anything unsaved in the window will be lost.',
    buttons: ['Restart Now', 'Later'],
    defaultId: 0,
    cancelId: 1,
  });
  if (response === 0) install();
}

/**
 * Wire the updater up.
 *
 * `notify` fires on every state change so the tray label and menu can
 * follow along without this module knowing anything about them.
 */
function start({ notify } = {}) {
  if (typeof notify === 'function') onStateChange = notify;

  if (!updatesSupported()) {
    setState('unsupported');
    return;
  }

  try {
    ({ autoUpdater } = require('electron-updater'));
  } catch (err) {
    // A packaging mistake that dropped the dependency must not take the
    // whole app down on launch.
    console.warn('[updater] electron-updater unavailable:', err && err.message);
    setState('unsupported');
    return;
  }

  autoUpdater.autoDownload = true;
  // We publish a full installer, never a web installer. Saying so silences
  // a warning on every check and stops electron-updater keeping a code path
  // alive for a package that does not exist.
  autoUpdater.disableWebInstaller = true;
  // If they never click Restart, apply it on the next quit rather than
  // making them do it twice.
  autoUpdater.autoInstallOnAppQuit = true;
  // Silent by default: a student has no use for updater chatter. Set
  // IP_UPDATER_LOG to a path to get the real reason a download failed —
  // electron-updater swallows a lot, and "it just did not update" is
  // otherwise impossible to diagnose from a bug report.
  autoUpdater.logger = updaterLogger();

  autoUpdater.on('update-available', () => setState('downloading'));
  autoUpdater.on('update-not-available', () => setState('idle'));
  autoUpdater.on('download-progress', () => setState('downloading'));
  autoUpdater.on('update-downloaded', (info) => setState('ready', info && info.version));
  autoUpdater.on('error', (err) => {
    setState('idle');
    console.warn('[updater] error:', err && err.message);
  });

  setTimeout(() => check(), FIRST_CHECK_DELAY_MS);
  timer = setInterval(() => check(), CHECK_INTERVAL_MS);
}

function stop() {
  if (timer) clearInterval(timer);
  timer = null;
}

module.exports = { start, stop, check, install, status, promptInstall, updatesSupported };
