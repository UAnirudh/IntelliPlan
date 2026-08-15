# IntelliPlan 📚

> **The AI-powered student planner built by a student, for students.**  
> Connect your school platform, get a personalized study schedule, and actually stay on top of your work.

🌐 **Live at:** [intelliplan.tech](https://intelliplan.tech)  
💬 **Community:** [discord.gg/34FYWhJQMU](https://discord.gg/34FYWhJQMU)  
📱 **Installable** as a PWA on Android & iOS · Chrome Extension available

---

## The Problem

Canvas and StudentVue show you *what's due*.  
They don't tell you *when to do it*, *what to prioritize*, or *how long it'll take*.

Students are left manually juggling deadlines across apps, guessing what to work on first, and falling behind — not because they're lazy, but because no tool connects the dots for them.

---

## The Solution

IntelliPlan pulls your assignments directly from Canvas, Google Classroom, StudentVue, Schoology, Blackboard, or Moodle, scores them by priority, and uses AI to generate a full weekly study schedule tailored to your workload and available hours. It syncs to Google Calendar, integrates with Notion, and includes a built-in AI tutor — all free.

---

## Features

### 📋 Dashboard
A Notion-style kanban board with three columns — **Overdue**, **Today**, and **Upcoming**. Assignments auto-import from your connected school platform and are sorted by AI priority scoring. You can also add manual tasks directly.

### 🗓 AI Scheduler
Input your available hours per day and preferred study time (morning, afternoon, evening). The AI generates a complete multi-day study plan — broken into focused work blocks with breaks — and exports it directly to **Google Calendar** with one click. Saved schedules persist across sessions.

### 📖 Study & Learn
Upload course notes (PDF, DOCX, TXT, MD, CSV) or paste text, and the AI generates:
- **Flashcards** for active recall
- **Key concepts** with definitions
- **Practice quiz questions** at varying difficulty levels
- **Summaries** of uploaded notes

Includes a full spaced repetition system (SRS) that tracks mastery level per question and schedules reviews automatically.

### 🎯 Priority View
Smart priority scoring (High / Medium / Low) with estimated time per assignment based on points possible, due date proximity, and course weight. See everything ranked so you always know what to tackle first.

### 🏫 Classes View
Browse and filter all assignments by course. Works across Canvas, Google Classroom, StudentVue, Schoology, Blackboard, and Moodle simultaneously if multiple accounts are linked.

### 📊 Grades & Grade Modeler
GPA overview pulled live from your school platform. The **Grade Modeler** lets you simulate "what if I get X% on my next test?" and shows you exactly how it affects your course grade and GPA.

### 🤖 Plani — AI Tutor
A dedicated AI tutor (separate from the assistant chatbot) that teaches subjects step by step — math, science, history, English, computer science, languages, economics, and test prep. It never just hands over answers; it builds understanding and checks comprehension with follow-up questions.

### 🔔 Push Notifications
Assignment deadline reminders sent as push notifications, even when the app is closed. Supports notification silencing for custom durations.

### 🌙 Dark Mode
Full light/dark theme support with preference saved across sessions.

---

## Integrations

| Integration | What It Does |
|---|---|
| **Canvas LMS** | Auto-imports assignments, due dates, points, and course names via REST API |
| **Google Classroom** | Auto-imports active courses and coursework via Google OAuth |
| **StudentVue** | Auto-imports assignments and missing work via the StudentVue API |
| **Schoology** | Auto-imports assignments via API key + secret |
| **Blackboard Learn** | Connects to an institution's Learn URL with OAuth 2.0 and imports gradebook due dates |
| **Moodle** | Connects to a Moodle URL plus web-services token and imports assignments/events |
| **Google Calendar** | One-click export of AI-generated study schedules; OAuth 2.0 with PKCE |
| **Notion** | Two-way task sync with your Notion databases via integration token |
| **Chrome Extension** | Badge count showing pending assignments; injects into Canvas and StudentVue pages |
| **PWA (Android)** | Installable from Chrome's install prompt, or Menu → Add to Home screen |
| **PWA (iOS)** | Installable via Safari Add to Home Screen |
| **Desktop app** | Native Windows, macOS and Linux builds — tray, global shortcut, OS notifications, offline plan. See [desktop/README.md](desktop/README.md) |

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python, Flask |
| **Database** | PostgreSQL (production via Railway), SQLite (local dev) |
| **ORM** | Flask-SQLAlchemy |
| **Auth** | Flask-Login, Flask-Bcrypt, Google OAuth 2.0 (PKCE), JWT tokens |
| **Sessions** | Flask-Session (SQLAlchemy-backed for persistence across Railway container restarts) |
| **AI — Scheduling & Study** | Google Gemini 2.5 Flash (Groq Llama fallback) |
| **AI — Vision / Image Notes** | Google Gemini 2.5 Flash (Groq Llama 4 Scout fallback) |
| **AI — Tutor & Chatbot** | Google Gemini 2.5 Flash (Groq Llama fallback) |
| **School APIs** | Canvas LMS REST API, Google Classroom API, StudentVue API, Schoology API, Blackboard Learn REST API, Moodle Web Services |
| **Calendar** | Google Calendar API v3 |
| **Notes** | Notion API |
| **Push Notifications** | Web Push / VAPID (pywebpush) |
| **Error Tracking** | Sentry |
| **Rate Limiting** | Flask-Limiter |
| **Frontend** | Vanilla HTML/CSS/JS, SVG animations, CSS custom properties |
| **Hosting** | Railway |

---

## Project Structure

```
IntelliPlan/
│
├── App.py                      # Main Flask app — all routes, models, config
├── ai_provider.py              # Unified AI layer — Gemini primary, Groq fallback
├── analytics.py                # PostHog analytics wrapper (no-ops when unconfigured)
├── streak_engine.py            # Pure streak logic — no Flask/DB dependencies
├── auth_api.py                 # Auth blueprint — JWT token endpoints
├── chatbot_api.py              # Plani chatbot + tutor API blueprint
├── google_calendar_helper.py   # Google OAuth + Calendar API helpers
├── notion_helper.py            # Notion API integration helpers
├── studentvue_helper.py        # StudentVue scraping/API helpers
├── canvas_routes.py            # Canvas-specific route helpers
├── schoology_helper.py         # Schoology API helpers
├── requirements.txt            # Python dependencies
├── Procfile                    # Railway/Gunicorn process config
│
├── Main_Project/
│   └── templates/              # Jinja2 HTML templates
│       ├── base.html           # Base layout with nav, Plani chatbot widget
│       ├── landing.html        # Public landing page
│       ├── dashboard.html      # Main task kanban board
│       ├── scheduler.html      # AI schedule generator
│       ├── study.html          # Study & Learn flashcard/quiz interface
│       ├── tutor.html          # Plani AI tutor chat interface
│       ├── priority.html       # Priority view
│       ├── classes.html        # Classes/courses view
│       ├── grades.html         # GPA overview
│       ├── grademodel.html     # Grade modeler simulator
│       ├── settings.html       # Integrations and account settings
│       └── ...                 # Login, register, connect, legal, blog pages
│
├── static/
│   ├── css/                    # Stylesheets including mobile.css
│   ├── icons/                  # PWA icons
│   ├── sw.js                   # Service worker for PWA/offline support
│   ├── manifest.json           # PWA manifest
│   ├── robots.txt
│   ├── sitemap.xml
│   └── llms.txt                # AI crawler instructions
│
├── extension/                  # Chrome extension source
│   └── IntelliPlan-Extension-V.2.zip
│
└── uploads/
    └── course_notes/           # Uploaded note files per user
```

---

## Getting Started (Local Development)

### Prerequisites
- Python 3.11+
- A Google Gemini API key (free at [aistudio.google.com/apikey](https://aistudio.google.com/apikey))
- Optional: Groq API key as fallback ([console.groq.com](https://console.groq.com))
- Canvas API token, Google Classroom OAuth, StudentVue credentials, Schoology API key, Blackboard OAuth, or Moodle web-services token (at least one)

### Installation

```bash
# Clone the repo
git clone https://github.com/UAnirudh/IntelliPlan.git
cd IntelliPlan

# Install dependencies
pip install -r requirements.txt

# Create a .env file
cp .env.example .env
# Fill in your keys (see Environment Variables below)

# Run the app
python App.py
```

The app runs on `http://localhost:3000` by default.

### Environment Variables

```env
SECRET_KEY=your-flask-secret-key
GEMINI_API_KEY=your-gemini-api-key
GROQ_API_KEY=your-groq-api-key-optional-fallback
DATABASE_URL=sqlite:///intelliplan.db

# Google OAuth (optional — needed for Google Calendar)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:3000/oauth2callback

# Google Classroom OAuth (optional)
GOOGLE_CLASSROOM_CLIENT_ID=
GOOGLE_CLASSROOM_CLIENT_SECRET=

# Blackboard Learn OAuth (optional)
BLACKBOARD_CLIENT_ID=
BLACKBOARD_CLIENT_SECRET=

# Moodle web services (optional; enabled by default)
MOODLE_ENABLED=1

# IndexNow (optional; defaults are already set for intelliplan.tech)
INDEXNOW_KEY=15d38c49db0d48efa4ec2ad2635b43c9
INDEXNOW_KEY_LOCATION=https://intelliplan.tech/15d38c49db0d48efa4ec2ad2635b43c9.txt
INDEXNOW_ENDPOINT=https://api.indexnow.org/indexnow

# Push Notifications (optional)
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
VAPID_EMAIL=

# Sentry (optional)
SENTRY_DSN=

# Lifecycle email (optional; required before the newsletter will send)
RESEND_API_KEY=
RESEND_FROM=IntelliPlan <noreply@intelliplan.tech>
MARKETING_POSTAL_ADDRESS=
CRON_SECRET=

# App URL
APP_BASE_URL=http://localhost:3000
```

---

## Lifecycle Email

Three emails, in `intelliplan/email/`. Templates live in
`Main_Project/templates/emails/`, plain-text bodies beside the code in
`intelliplan/email/text/`.

| Email | Key | Trigger | Consent |
|---|---|---|---|
| Welcome | `welcome` | Cron, signups in the last 36h | Transactional — no marketing opt-in needed |
| Feedback request | `feedback_v1` | Cron, accounts 14–15 days old **with real activity** | Requires `marketing_emails_opt_in` |
| Newsletter | `newsletter_YYYY_MM` | Admin only, never automatic | Requires `marketing_emails_opt_in` |

Every send passes `intelliplan.email.eligibility.is_marketing_eligible`,
which refuses on unknown age, under-13 without parental consent, undated
consent, a non-student role, or a suppressed address. Sends are deduplicated
on `(user_id, email_key)` in `email_sends`, so a double cron fire cannot
double-send.

### Cron

Add one Railway cron entry, daily at 16:00 UTC (≈ 9am PT):

```bash
curl -X POST https://intelliplan.tech/cron/lifecycle-emails -H "X-Cron-Secret: $CRON_SECRET"
```

Railway schedule expression: `0 16 * * *`. It is safe to run more often —
the ledger makes repeat runs no-ops.

### Sending the newsletter

Admin-only, and never in one step. Dry run first:

```bash
curl -X POST https://intelliplan.tech/api/admin/newsletter/preview -H 'Content-Type: application/json' -d @newsletter.json
```

That returns the recipient count and sends nothing. Then test it on
yourself — `test: true` mails only `ADMIN_EMAILS` and does not write the
ledger, so it is repeatable. Only a payload with `"confirm": true` sends to
the full list; without it `/api/admin/newsletter/send` returns 409 and the
dry-run count instead.

`MARKETING_POSTAL_ADDRESS` must be set or the newsletter and feedback sends
refuse to run — CAN-SPAM requires a physical address in commercial email.

### Unsubscribe

`GET|POST /email/unsubscribe/<token>` works logged-out, in one click, and
the token never expires. Unsubscribing sets `marketing_emails_opt_in=False`
and adds the **address** to `email_suppressions` — keyed on the address, not
the user, so deleting and recreating an account stays suppressed. Deadline
reminders are transactional and are deliberately unaffected.

---

## Mobile App (Expo / React Native)

An Expo app lives in `mobile/`. It authenticates with a bearer token from
`/api/v1/auth/token` and reads `/api/today` and `/api/grade-predictions`.

```bash
cd mobile
npm install
npm start          # then press a for Android, i for iOS
```

It points at production by default. Under `__DEV__` it uses the dev server
— `10.0.2.2:5000` on an Android emulator, `localhost:5000` on iOS — and
`EXPO_PUBLIC_API_BASE` overrides both.

```bash
npx expo-doctor                          # 17/17 should pass
npx tsc --noEmit                         # typecheck
npx expo export --platform android       # verify it bundles
```

Not on the app stores yet. `eas.json` has development, preview (APK) and
production (AAB) profiles ready for `eas build`.

---

## API Overview

### School Data
| Endpoint | Method | Description |
|---|---|---|
| `/live` | GET | Fetch live assignments from connected platform |
| `/tasks/unified` | GET | All tasks merged (platform + manual + Notion) |
| `/courses` | GET | List of courses |
| `/grades/data` | GET | Grade data per course |
| `/missing/data` | GET | Missing/overdue assignments |

### AI
| Endpoint | Method | Description |
|---|---|---|
| `/generate_schedule` | POST | Generate AI study schedule from assignments |
| `/api/tutor` | POST | Plani tutor — multi-turn academic tutoring |
| `/api/chatbot` | POST | Plani assistant — IntelliPlan feature help |
| `/study/generate` | POST | Generate flashcards + quiz from notes |
| `/study/evaluate` | POST | AI-evaluate a student's quiz answer |
| `/study/analyze-image` | POST | Extract text/content from an uploaded image |
| `/notes/<id>/summarize` | POST | AI summarize uploaded course notes |

### Tasks
| Endpoint | Method | Description |
|---|---|---|
| `/tasks/manual/create` | POST | Create a manual task |
| `/tasks/manual/update` | POST | Update a manual task |
| `/tasks/manual/delete` | POST | Delete a manual task |
| `/dismiss` | POST | Dismiss/complete an assignment |
| `/restore` | POST | Restore a dismissed assignment |

### Integrations
| Endpoint | Method | Description |
|---|---|---|
| `/oauth/google` | GET | Start Google OAuth flow |
| `/api/lms/connect/<provider>` | POST | Start Google Classroom/Blackboard OAuth or return Moodle manual-connect metadata |
| `/api/lms/connect/moodle/manual` | POST | Connect Moodle with site URL + web-services token |
| `/api/lms/status/google_classroom` · `/blackboard` · `/moodle` | GET | Per-provider connection status (one route each, not a wildcard) |
| `/api/lms/disconnect/google_classroom` · `/blackboard` · `/moodle` | POST | Disconnect that provider |
| `/api/lms/providers` | GET | Every provider the registry knows about |
| `/api/lms/status` | GET | Which providers this user is connected to |
| `/api/lms/<key>/sync` | POST | Sync one provider through the registry |
| `/api/lms/<key>/disconnect` | POST | Disconnect one provider through the registry |
| `/<INDEXNOW_KEY>.txt` | GET | Host the IndexNow verification key at the site root |
| `/indexnow` | GET | IndexNow protocol documentation and IntelliPlan setup |
| `/api/admin/indexnow/status` | GET | Admin-only — show key, host, endpoint, sitemap count |
| `/api/admin/indexnow/submit` | POST/GET | Admin-only — bulk POST `{"urls":[...]}`, GET `?url=...`, or full sitemap |
| `/calendar/export` | POST | Export schedule to Google Calendar |
| `/notion/connect` | POST | Connect Notion integration |
| `/notion/tasks` | GET | Fetch Notion tasks |
| `/schedule/save` | POST | Save generated schedule |
| `/schedule/saved` | GET | Load saved schedule |

### Chrome Extension
| Endpoint | Method | Description |
|---|---|---|
| `/extension/login` | POST | Authenticate extension with email/password |
| `/extension/tasks` | GET | Get tasks for badge count |
| `/extension/schedule` | GET | Get saved schedule |
| `/extension/grades` | GET | Get grades |
| `/extension/dismiss` | POST | Dismiss task from extension |

---

## Chrome Extension

The IntelliPlan Chrome Extension:
- Shows a **badge count** of pending assignments on the extension icon
- **Injects directly into Canvas and StudentVue pages** for quick access
- Supports login with your IntelliPlan account
- Available as a `.zip` in the repo — load via Chrome's `chrome://extensions` in Developer Mode

---

## Task-Completion Streaks (Experiment)

IntelliPlan includes an optional Duolingo-style streak system behind the `streak_v1` feature flag. It is **off by default** in production.

### Enabling for testing

1. Log in as an admin and visit the admin panel, or
2. Set the flag directly in the database:
   ```sql
   UPDATE feature_flags SET enabled = true, rollout_percentage = 100 WHERE key = 'streak_v1';
   ```
3. Or set the environment-level override before starting the app:
   ```bash
   # .env
   STREAK_V1_ENABLED=1
   ```

### How it works

- **Qualifying actions:** completing a task or viewing the dashboard (plan review).
- **Streak freezes:** users start with 2 freezes (cap of 3). One freeze covers one missed day. A new freeze is earned every 7-day milestone.
- **Timezone-aware:** streaks are tracked in the user's local timezone (detected from the browser).
- **Rollout:** the flag supports percentage-based rollout via a deterministic SHA-256 hash on `streak_v1:{user_id}`.

### Streak API endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/streak/status` | GET | Current streak, week dots, nudge eligibility |
| `/api/streak/plan-review` | POST | Record a dashboard view as a qualifying action |
| `/api/streak/set-timezone` | POST | Persist the user's IANA timezone |
| `/api/streak/nudge-shown` | POST | Mark today's nudge as shown |
| `/api/streak/nudge-tapped` | POST | Track nudge tap |
| `/api/streak/pill-tapped` | POST | Track streak pill tap |

### Running streak tests

```bash
# Unit tests (pure logic, no DB)
pytest test_streak_engine.py -v

# E2E tests (Flask test client + SQLite in-memory)
pytest test_streak_e2e.py -v
```

### Analytics events (PostHog)

When `POSTHOG_API_KEY` is set, the following events fire server-side: `streak_started`, `streak_continued`, `streak_broken`, `streak_freeze_consumed`, `streak_freeze_earned`, `nudge_shown`, `nudge_tapped`, `streak_pill_tapped`. The user property `streak_v1_cohort` (`treatment` or `control`) is set on each streak status fetch.

---

## Contributing

This project was built solo as a student project. Contributions, bug reports, and feature requests are welcome.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes (`git commit -m 'Add your feature'`)
4. Push to the branch (`git push origin feature/your-feature`)
5. Open a Pull Request

For bugs or ideas, open an [Issue](https://github.com/UAnirudh/IntelliPlan/issues) or join the [Discord](https://discord.gg/34FYWhJQMU).

---

## Roadmap

Everything the previous roadmap listed has shipped and moved up into
**Features** and **Integrations**. What is left is what has not been done
yet — each item says what specifically is missing, so it is obvious when it
can be ticked.

### Next

- [ ] **Ship the mobile app.** The Expo app in `mobile/` builds and bundles
      for Android and passes all 17 expo-doctor checks; `eas.json` is in
      place. What remains is an Apple Developer and Google Play account,
      store listings, and a first submission.
- [ ] **Desktop auto-update.** `electron-updater` fits the existing
      electron-builder config and the release feed already exists. It was
      blocked on the installer bug, which is now fixed.
- [ ] **Code-sign the desktop builds.** The CI plumbing is written and
      driven by repository secrets; what is missing is a certificate. Azure
      Trusted Signing (~$10/month) is the route that actually stops the
      SmartScreen warning — an OV certificate does not. macOS needs a
      Developer ID before Gatekeeper stops refusing the first launch.
- [ ] **Onboarding and newsletter email.** Signup now records marketing
      consent with a timestamp; nothing sends to it yet.

### Later

- [ ] **Native ARM64 Windows installer.** Blocked upstream: electron-builder
      26.15.3 produces an ARM64 NSIS package that installs no executable.
      Snapdragon and Copilot+ PCs currently run the x64 build under
      translation. Revisit when upstream fixes it — see
      [desktop/README.md](desktop/README.md) for what was ruled out.
- [ ] **More SIS coverage.** The provider registry makes each new one small;
      Infinite Campus and Skyward are the most asked for.
- [ ] **Accessibility audit.** No WCAG pass has been done end to end.
- [ ] **Study-group depth.** Shared tasks and voice rooms exist; shared
      schedules and group progress do not.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">
  Built by a student, for students. 🎓<br/>
  <a href="https://intelliplan.tech">intelliplan.tech</a> · <a href="https://discord.gg/34FYWhJQMU">Discord</a> · <a href="https://github.com/UAnirudh/IntelliPlan/issues">Report a Bug</a>
</div>
