# IntelliPlan for desktop

A native application for Windows, macOS, and Linux. Not a PWA and not a
shortcut to the website — it runs as a real OS application with its own
process, tray presence, menus, and notification pipeline.

## What installing it actually buys a student

The web app is rendered in the window, but these only exist here:

| Capability | Why it needs a native app |
| --- | --- |
| **OS notifications** | Fire whether or not a window is open. The browser's Notification API only works while the page is alive, so a closed tab means a missed session reminder. |
| **Tray "up next"** | The next scheduled session is visible without opening anything. Refreshes every minute and on wake from sleep. |
| **Global shortcut** | `Ctrl/Cmd+Shift+S` starts a study session from any application. |
| **Persistent session** | Sign in once. The session survives quitting and restarting. |
| **Offline handling** | A dropped connection shows a real message and reconnects, not Chromium's error page. |
| **Deep links** | `intelliplan://active` opens straight to the session screen. |

## Running it

```bash
cd desktop
npm install
npm start
```

Point it at a local server instead of production:

```bash
IP_TARGET=http://127.0.0.1:3000 npm start
```

## Building installers

```bash
npm run dist:win      # NSIS installer + portable exe (x64, arm64)
npm run dist:mac      # dmg + zip (Intel, Apple Silicon)
npm run dist:linux    # AppImage, deb, rpm (x64, arm64)
```

Each platform's installers must be built on that platform (or in CI with
the matching runner) — electron-builder cannot produce a signed macOS build
from Windows.

## Security posture

The renderer is sandboxed: `contextIsolation: true`, `nodeIntegration:
false`, `sandbox: true`. It reaches the main process only through the three
functions in `src/preload.js`.

Two decisions worth knowing about:

- **No generic IPC channel is exposed.** A bridge that forwards arbitrary
  channel names from page script is the usual route to remote code
  execution in an Electron app. `notify()` accepts a title, a body, and a
  same-app path — a full URL is rejected, so page script cannot ask the
  main process to navigate the window somewhere else.
- **Navigation is pinned to the app's origin.** Anything else opens in the
  system browser. This keeps a stray link out of the application shell, and
  it is also required for Google sign-in, which refuses to run in an
  embedded view.

Permission requests are answered with an allowlist (notifications, media
for the focus check-in, fullscreen, sanitised clipboard writes); everything
else is denied without prompting.

## Known limitations

- **Not code-signed.** Windows SmartScreen and macOS Gatekeeper will warn
  until certificates are configured. Signing needs an Apple Developer ID
  and a Windows code-signing certificate, which are credentials rather than
  code.
- **No auto-update yet.** `electron-updater` fits the existing
  electron-builder config, but it needs a release feed to publish to.
- Icons are the 512px PWA icon. Platform-native `.icns` and `.ico` sets
  would render more crisply at small sizes.
