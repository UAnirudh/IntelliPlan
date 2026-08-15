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

## Windows targets, and the ARM64 installer that does not work

Windows ships **one NSIS installer, x64 only**, plus an **arm64 zip**. That
looks like a downgrade and is deliberate.

electron-builder 26.15.3 cannot produce a working ARM64 NSIS package. The
installer builds, runs, exits 0, and reports success — while writing every
`.pak`, locale and `app.asar` and silently omitting `IntelliPlan.exe` and
all eight DLLs. Users got a Start Menu shortcut pointing at a file that was
never installed ("Windows is searching for IntelliPlan.exe"). This shipped
in 1.0.0.

What was ruled out, so nobody repeats it:

| Suspect | Verdict |
| --- | --- |
| Truncated or corrupt payload | No. `7za l` shows all 76 files / 370 MB present, and `7za e` extracts the 214 MB exe intact. |
| Windows Defender | No. Zero detection events during install; manually extracted copies of the same binaries persist. |
| Disk space, Controlled Folder Access, third-party AV | No. 39 GB free, CFA off, Defender only. |
| Written then deleted | No. Polling the directory every 700 ms during install shows `IntelliPlan.exe` never appears at all. |
| `${IsNativeARM64}` misdetecting the CPU | No. A probe built with the bundled NSIS returns `IsNativeARM64=TRUE`, `nativeArch=0xAA64`. |
| `nsis.useZip: true` as a workaround | Worse. The installer hangs indefinitely, zero files written. |

The x64 installer works correctly **on ARM64 hardware**, verified by
installing and running it on a Snapdragon X1E80100. Windows' x64 translation
covers the gap at some cost in speed, and the arm64 zip is there for anyone
who wants the native binary and can live without shortcuts or auto-update.

Revisit when electron-builder fixes ARM64 NSIS: restore `arm64` to
`build.win.target[0].arch` and drop the zip.

The `portable` target was removed at the same time. It shared `artifactName`
with the NSIS target, so it never appeared in a release at all.

**The build now smoke-tests its own installer.** `.github/workflows/desktop-release.yml`
installs the result to a temp directory and fails the job unless
`IntelliPlan.exe` and at least one DLL are actually there. An installer that
lies about succeeding must not be publishable again.

## Code signing

Builds are unsigned by default: there is no certificate in this repo, so
CI resolves to no signing flags and sets
`CSC_IDENTITY_AUTO_DISCOVERY=false` to stop electron-builder hunting the
runner's keychain for an identity that is not there. `/download` tells
students what SmartScreen and Gatekeeper will do about that, which is the
honest alternative to a scary warning they were not expecting.

Signing turns on by adding repository secrets. The workflow reads them and
signs; nothing else changes. Nothing here needs a code change.

### Windows — what actually silences SmartScreen

The warning is not about *whether* the file is signed. SmartScreen scores
the **reputation** of the certificate that signed it, and an unknown
certificate scores the same as no certificate until enough people have
installed past the warning anyway. So the choice is about which route
starts with reputation:

| Route | Cost | SmartScreen |
| --- | --- | --- |
| **Azure Trusted Signing** | ~$10/month | Trusted immediately. The one to pick. |
| EV certificate | $300–600/yr | Trusted immediately, but the key lives on a hardware token a CI runner cannot hold. |
| OV certificate (`.pfx`) | $200–400/yr | Signed, but still warns until reputation builds. Buys little over unsigned. |

Azure Trusted Signing requires an Azure subscription and identity
validation — an organisation needs three years of verifiable history, and
an individual validates against government ID. Then create a Trusted
Signing account and certificate profile, register an app registration with
the *Trusted Signing Certificate Profile Signer* role, and set:

| Secret | Value |
| --- | --- |
| `AZURE_CODE_SIGNING_ACCOUNT` | Trusted Signing account name |
| `AZURE_CODE_SIGNING_PROFILE` | Certificate profile name |
| `AZURE_CODE_SIGNING_ENDPOINT` | Region endpoint, e.g. `https://eus.codesigning.azure.net` (optional, defaults to East US) |
| `AZURE_CODE_SIGNING_PUBLISHER` | Publisher name on the certificate (optional) |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | App registration credentials |

For a plain `.pfx` instead, set `CSC_LINK` (base64 of the file, or a URL)
and `CSC_KEY_PASSWORD`. Read the table above first — an OV certificate
does not stop the warning.

### macOS

`CSC_LINK` and `CSC_KEY_PASSWORD` hold the Developer ID Application
certificate. Gatekeeper also wants the app notarised, which is a second
credential: set `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, and
`APPLE_TEAM_ID` and the workflow adds `-c.mac.notarize=true`. Signing
without notarising still warns, so set all four or none.

### Checksums

Every release carries `SHA256SUMS.txt` covering the platforms that built.
It is not a substitute for signing — anyone who can replace the installer
can replace the checksum file — but it does let someone confirm a download
arrived intact, and it is the only integrity signal an unsigned build has.

## Google sign-in

Google refuses to run OAuth inside an embedded browser view, and enforces
that hardest against supervised Family Link accounts — which is a large
share of the students this app exists for. The window therefore never
shows Google's sign-in page. `accounts.google.com` is not in the internal
allowlist, and a click on the site's own "Sign in with Google" link is
intercepted by URL in `attachNavigationGuards`, so the website's login
template needs no knowledge of the desktop client.

The round trip:

1. `startGoogleSignIn()` invents a PKCE verifier, keeps it in memory, and
   opens `<origin>/login/google?desktop=<challenge>` in the **system
   browser**.
2. The browser completes Google's flow against the website as normal.
3. The callback sees the stored challenge, mints a one-time code, and
   redirects to `intelliplan://auth?code=…`.
4. The app redeems the code at `POST /api/desktop/auth/exchange`, sending
   the verifier. The request runs as a `fetch` inside the window so the
   session cookie lands in the jar the app actually browses with.

Any local program can register `intelliplan://`, so the code on that deep
link must be assumed readable by a hostile app. It is only half a
credential: redeeming also needs the verifier, which never leaves the
process. Codes are single-use, expire in two minutes, are stored only as
SHA-256, and a code presented with the wrong verifier is burned rather
than left live. The rules live in `desktop_auth.py`, tested in
`tests/test_desktop_auth.py` and `tests/test_desktop_auth_exchange.py`.

The app claims the `intelliplan://` scheme itself on every start rather
than relying on the installer, since sign-in now depends on that handler
existing.

## Icons

`build/` is electron-builder's `buildResources` directory: it reads
`icon.png` from there to brand the executable and installer, and then
deliberately does **not** copy it into the app. The runtime needs the same
images for the window and the tray, so `extraResources` ships a copy beside
`app.asar` and `iconPath()` checks `process.resourcesPath` first.

Without that the failure is quiet and easy to miss in development, where
`build/` is right there on disk: installed copies fall back to Electron's
default icon and `createTray()` returns early, taking the tray feature with
it.

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

- **Not code-signed yet.** Windows SmartScreen and macOS Gatekeeper warn
  until the certificates above are configured. The CI plumbing is done —
  what is missing is the credentials, not the code.
- **No auto-update yet.** `electron-updater` fits the existing
  electron-builder config, but it needs a release feed to publish to.
- Icons are the 512px PWA icon. Platform-native `.icns` and `.ico` sets
  would render more crisply at small sizes.
