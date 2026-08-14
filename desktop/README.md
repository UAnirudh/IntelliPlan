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

## Publishing a release

Tag it. `.github/workflows/desktop-release.yml` builds all three platforms
on their own runners and attaches the installers to a GitHub Release.

```bash
npm version patch --no-git-tag-version   # bump desktop/package.json
git commit -am "chore(desktop): 1.0.1"
git tag -a desktop-v1.0.1 -m "IntelliPlan desktop 1.0.1"
git push origin main desktop-v1.0.1
```

The tag prefix matters: `desktop_releases.py` only considers tags starting
`desktop-v`, so a web or extension tag is never mistaken for an app build.

The website picks the release up on its own — `/download` reads the latest
release from the GitHub API and lists whatever assets it finds, so there is
nothing to update by hand and no filenames to keep in sync. A platform that
failed to build is simply absent from the page rather than a dead link.

Four fields in `package.json` are load-bearing for packaging, all of which
this project has been bitten by:

| Field | Why |
|---|---|
| `homepage` | deb and rpm refuse to build without it |
| `author.email` | becomes the deb/rpm maintainer field; also required |
| `repository` | electron-builder warns and cannot infer the publish target |
| `build.publish` | without a provider, update-info generation dereferences null |

Builds are **unsigned**. There is no certificate in this repo, and
`CSC_IDENTITY_AUTO_DISCOVERY=false` in CI keeps electron-builder from
hunting for one and failing. `/download` tells students what SmartScreen
and Gatekeeper will do about that, which is the honest alternative to a
scary warning they were not expecting.

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
