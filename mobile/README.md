# IntelliPlan Mobile

The IntelliPlan phone app: React Native on Expo, talking to the same Flask
API the website runs on. One codebase for iOS, Android and web.

```bash
cd mobile
npm install
npx expo start
```

Scan the QR code with **Expo Go**, or press `i` / `a` / `w` for the iOS
simulator, Android emulator, or a browser.

## What's in it

Five tabs, in the order a school day happens:

| Tab | What it does |
| --- | --- |
| **Today** | The Command Center — AI briefing, Academic Health dial, a 7-day workload forecast, and the ranked "do this first" list with `why_now` on each item. Falls back to a plain due-list if the briefing feature isn't enabled for the account. |
| **Due** | Every assignment from every connected platform, grouped Overdue / Today / This week / Later, filterable by window and by course. Tap the circle to complete; the Done filter undoes it. |
| **Plan** | The saved study plan, plus a generator (hours per day × when you focus best). Generating is a button — never automatic on open, so it can't quietly discard progress ticked off against the existing plan. |
| **Grades** | Course grades with a percentage bar per course, and a Forecast tab with per-course predictions, trend, and the confidence behind each number. |
| **Plani** | The AI tutor, plus Snap & Solve: photograph a worksheet and it works through every problem it can see. |

Plus a profile sheet (streak, sparks, level, learning profile, push
notifications, light/dark/system) and an add-task modal.

## How it talks to the server

Everything goes through `lib/api.ts`. Sign-in exchanges email + password for
a bearer token at `POST /api/v1/auth/token`; the token is stored in the
device keychain via `expo-secure-store` (web falls back to AsyncStorage,
which is the best the browser offers).

Flask's `request_loader` in `App.py` resolves that bearer for *every*
`@login_required` view, not just `/api/v1/*`. That is what lets the app use
`/api/today`, `/api/grade-predictions` and `/api/tutor` directly instead of
maintaining a parallel half-implementation that drifts from the website.

Two behaviours worth knowing about, both in `lib/useQuery.ts`:

* **Every successful response is cached.** Opening the app with no signal
  shows the last version that loaded behind an "Offline" notice, rather
  than a spinner into an error page.
* **404 is not an error.** Several endpoints sit behind feature flags, so a
  404 means "your school hasn't switched this on", which gets its own
  screen instead of "something broke".

A network failure at launch does *not* sign you out — only a credential the
server actively rejects does. See `lib/auth.tsx`.

## Configuration

`lib/config.ts` defaults to production and only uses dev-machine addresses
under `__DEV__`, so a release build can never ship pointing at localhost.
Override for any build with:

```bash
EXPO_PUBLIC_API_BASE=https://staging.intelliplan.tech npx expo start
```

For a local Flask server: Android emulators reach the host on
`http://10.0.2.2:5000`, iOS simulators on `http://localhost:5000` — both
are already the `__DEV__` defaults. A **physical phone on Expo Go** reaches
neither: use your machine's LAN address, e.g.
`EXPO_PUBLIC_API_BASE=http://192.168.1.20:5000 npx expo start`.

## Push notifications

`lib/push.ts` registers an Expo push token against
`POST /api/v1/push/register`, which stores it as a `PushSubscription` row —
the same table browser subscriptions use, so everything that already sends
a reminder reaches the phone with no change at the call site. Signing out
unregisters the token first, so a borrowed phone stops receiving the
previous student's deadlines.

**Remote push does not work in Expo Go on Android** (SDK 53+). The toggle
in the profile sheet will say so rather than failing silently. Use a
development build to test it:

```bash
npx eas build --profile development --platform android
```

## Shipping

`eas.json` has three profiles:

```bash
npx eas build --profile development --platform all   # dev client, dev API
npx eas build --profile preview      --platform all  # internal APK / TestFlight
npx eas build --profile production   --platform all  # store builds
npx eas submit --profile production  --platform all
```

Before the first store submission:

1. `npx eas init` to create the EAS project — this fills in
   `extra.eas.projectId`, which `lib/push.ts` needs to mint push tokens.
2. Set `ios.appleTeamId` / ASC app id and the Play service-account key in
   `eas.json`'s `submit` block (or let `eas submit` prompt for them).
3. Bump `expo.version` for each store release. `autoIncrement` handles
   `buildNumber` / `versionCode`.

App icons in `assets/` are generated from `static/icons/icon-512.png`, so
the phone app carries the same mark as the website. Regenerate them with
`scripts/gen-icons.py` if the web icon changes.

## Layout

```
app/                 expo-router file routes
  (tabs)/            the five tabs
  login.tsx          sign in + sign up
  settings.tsx       profile sheet (modal)
  new-task.tsx       add a task (modal)
components/          UI kit — Card, Button, Chip, TaskRow, Ring, Bars…
theme/               design tokens ported from static/css/ip-base.css
lib/                 api client, auth, query cache, push, formatting
```

`theme/tokens.ts` is copied literally from the web palette rather than
re-derived, so the two clients can't drift apart on colour.
