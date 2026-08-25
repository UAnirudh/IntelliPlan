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
| **Plan** | The saved study plan, plus a generator (hours per day × when you focus best). Generating is a button — never automatic on open, so it can't quietly discard progress ticked off against the existing plan. Blocks tick off against the plan and the progress follows the account rather than one device. **Catch me up** re-solves the remaining week around the sessions that slipped, crediting what was actually done. **Set my own hours** opens the manual scheduler: hand-placed blocks, saved as named routines and applied to whichever days they fit — the real shape of a week is one or two routines repeated, not seven bespoke days. |
| **Grades** | Three segments: **Current** (a percentage bar per course), **Forecast** (per-course predictions with trend and the confidence behind each number), and **What you know** (mastery by subject, and the concepts most likely to have slipped since you last reviewed them). |
| **Plani** | The AI tutor with conversation history, plus Snap & Solve: photograph a worksheet and it works through every problem it can see. |

The modals over the tabs:

* **Focus** — a study timer against one piece of work. Start it from Today,
  from a task, or from a block in the plan; pause, resume, and finish with
  "finished it / made progress". The server is the clock of record: elapsed
  time is sent as a running total every 15 seconds, so a lost heartbeat
  costs nothing and the server clamps the figure against wall-clock time.
  Closing the app and reopening it resumes the session *paused* rather than
  claiming the gap as study time.
* **Task** — the ranking's own working: `why_now`, a weighted breakdown of
  what pushed the task up the list, and the actions (focus, complete, mark
  as a test). A task you typed yourself also offers **Edit**, which is
  where it can be renamed, re-dated, re-estimated or deleted. An
  assignment from Canvas does not: it is a copy of the teacher's record,
  so a rewrite here would only be undone by the next sync.
* **Your school** — connect, sync and disconnect Canvas, StudentVue,
  Schoology and the rest.
* **New task** — add something the platforms don't know about.
* **Accounts & calendar** — the connected Google accounts, and the school
  profiles. A student with more than one school account has exactly one
  active, and it decides whose assignments and grades every other screen
  is showing — so switching clears the response cache with it, or the
  previous school's work would sit under the new profile's name until each
  screen happened to refetch.
* **Study tools** — paste notes, pick a PDF, or drop a YouTube link, and
  get flashcards and a quiz. Three ways in, one pipeline: each source only
  produces the study text that `/study/generate` turns into key concepts
  and questions, so the cards and the quiz are two views of one result
  rather than two generations that could disagree. Answers are typed in
  the student's own words and marked semantically — an exact match would
  fail a correct answer for its wording.
* **Streak & shop** — the streak, the Sparks balance, and what Sparks buy.
  The catalogue and the week's discounted item come from the server with
  the balance, so nothing here holds a copy of a price. A broken streak
  can be bought back while the repair window is open.

Plus a profile sheet: streak, sparks, level and freezes, weekly quests,
focus-session history, the learning profile, reminders, and
light/dark/system.

## First run

Signing **up** goes to a three-step setup (`app/onboarding.tsx`): what the
app does, connect your school, and your grade level plus goals. Every step
is skippable — manual tasks, Plani and the focus timer all work with
nothing connected, so blocking entry on a Canvas login would be charging
admission for a door that is already open.

Signing **in** skips it. The onboarded flag is per-account and stored on
the device, so it is set at sign-in too: a student who has used
IntelliPlan for months and just installed it on a new phone does not need
the tour. The flag stays unset between sign-up and finishing setup, so
force-quitting halfway resumes there rather than dropping someone into an
app they have not configured.

## When something throws

`components/ErrorBoundary.tsx` is exported from the root layout as
`ErrorBoundary`, which is the name expo-router looks for. Without it a
release build shows a blank white screen with no way forward — the red box
with the stack trace is a development-only courtesy and the store build
has nothing in its place. It offers a retry (most of what can throw is a
render against an unexpected payload shape, which a refetch clears) and
shows the error message, which is the one thing that makes a student's bug
report reproducible.

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

Writes get the same treatment, in `lib/queue.ts`. Ticking an assignment
off with no signal used to fail with a buzz and no record, so the student
either did it twice or lost it — reads and writes degrading differently is
worse than either behaviour alone, because it makes the offline story
unpredictable. Now the row updates, the write is persisted, and it replays
when a list next loads successfully or the app is foregrounded.

Only failures that say nothing about the request are queued: status 0 (never
reached the server) and 502/503/504. A 400 means the server understood and
refused, so it is surfaced and the row rolls back — retrying that forever
would hide a real bug. Opposing edits made offline cancel out rather than
being sent as two requests the server might apply backwards, and signing
out clears the queue so a shared phone never replays one student's edits
into the next student's account.

A network failure at launch does *not* sign you out — only a credential the
server actively rejects does. See `lib/auth.tsx`.

### Connecting a school platform, or Google

Sync and disconnect are plain JSON calls, so the things a student does
repeatedly never leave the app. **Connecting** opens the website. That is
deliberate: the OAuth providers need a real browser redirect to be secure
at all — Google refuses to run its flow inside an embedded web view,
hardest of all against the supervised Family Link accounts these students
often have — and the credential providers already have a hardened form on
the site. A second implementation here would be a second place for school
credentials to leak.

What makes it one tap rather than a scavenger hunt is that the browser no
longer opens logged out. `POST /api/v1/link/session` mints a one-time
code; the app opens `/link/<code>`; the server signs that browser in as
the code's owner and forwards straight to the provider's OAuth start. When
the flow finishes the site redirects to `intelliplan://connected`, which
closes the browser and returns to the app, which refreshes and runs a
first sync so something actually appears.

Without that hand-off the browser arrives with no session — both OAuth
starts read the session cookie, not the bearer token the phone holds — and
the connection attaches to nobody.

The code is a bearer credential travelling in a URL, so `app_link.py`
makes it worth as little as possible: single use, ninety-second life,
stored only as a SHA-256, compared in constant time, and forwarded only to
an allow-listed internal path. That last one matters most — the endpoint
redirects *while authenticated*, so a caller-chosen destination would be
an open redirect that also hands over a live session. `tests/test_app_link.py`
covers each of those.

### Confirmations

`components/Confirm.tsx`, not `Alert.alert()`. React Native's own Alert is
a no-op on react-native-web — the implementation is an empty method — so
every confirmation built on it silently did nothing in the browser build.
One themed sheet renders on all three platforms instead: it matches the
rest of the app, it can be driven in a test, and there is only one
implementation to keep correct.

## Running it on your phone

```bash
cd mobile
npm install
npm run start:prod        # points at https://intelliplan.tech
```

Scan the QR with **Expo Go** (Android: in-app scanner; iOS: the Camera app).
Phone and laptop must be on the same wifi. If they are not — or the network
blocks peer traffic, which school and café wifi routinely does — use:

```bash
npm run start:tunnel
```

`npm start` on its own points at a **local** Flask server instead. That now
works from a real phone too: the app reads the LAN address Expo served the
bundle from and talks to port 5000 on that same machine, so
`python App.py` on your laptop is reachable with no configuration. Emulators
still fall back to `10.0.2.2` / `localhost`.

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

There are **two** reminder switches in the profile sheet, because they are
genuinely different mechanisms and collapsing them would mean one of them
silently does nothing:

* **Deadline reminders** (`lib/reminders.ts`) are scheduled on the device
  from the task list the app already fetched, for the evening before each
  deadline. They fire in Expo Go, they fire offline, and they cost nothing
  to deliver — the trade is that they only know about deadlines as of the
  last time you opened the app. The schedule is rebuilt from scratch every
  time the Due list loads, so finished work stops nagging you.
* **Server nudges** (`lib/push.ts`) are remote push, and **do not work in
  Expo Go on Android** (SDK 53+). The toggle says so rather than failing
  silently. Use a development build to test it:

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
  onboarding.tsx     three-step first run
  focus.tsx          study timer (full-screen modal)
  task.tsx           task detail + actions (modal)
  connect.tsx        school platforms + Google Calendar (modal)
  plan-custom.tsx    hand-placed study blocks (modal)
  settings.tsx       profile sheet (modal)
  new-task.tsx       add a task (modal)
components/          UI kit — Card, Button, Chip, TaskRow, Ring, Bars, Confirm,
                     ErrorBoundary…
theme/               design tokens ported from static/css/ip-base.css
lib/                 api client, auth, query cache, write queue, push, reminders,
                     onboarding, formatting
```

`theme/tokens.ts` is copied literally from the web palette rather than
re-derived, so the two clients can't drift apart on colour.
