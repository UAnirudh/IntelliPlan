# IntelliPlan — Project Overview

> **The AI-powered student planner built by a student, for students.**
> Connect your school platform, get a personalized study schedule, and actually stay on top of your work.

- 🌐 **Live at:** https://intelliplan.tech
- 📱 Installable as a PWA on Android & iOS · Chrome Extension available
- 🧑‍💻 Owner: Anirudh (this project is mine; the App Store release is being done on my behalf)

---

## The Problem

Canvas and StudentVue show you *what's due*. They don't tell you *when to do it*, *what to
prioritize*, or *how long it'll take*. Students juggle deadlines across apps, guess what to
work on first, and fall behind — not because they're lazy, but because no tool connects the
dots for them.

## The Solution

IntelliPlan pulls assignments directly from Canvas, StudentVue, or Schoology, scores them by
priority, and uses AI to generate a full weekly study schedule tailored to the student's
workload and available hours. It syncs to Google Calendar, integrates with Notion, and
includes a built-in AI tutor — all free.

---

## Features

- **📋 Dashboard** — A Notion-style kanban board (Overdue / Today / Upcoming). Assignments
  auto-import from the connected school platform, sorted by AI priority. Manual tasks too.
- **🗓 AI Scheduler** — Enter available hours + preferred study time; AI builds a multi-day
  study plan in focused blocks with breaks, exportable to Google Calendar in one click.
- **📖 Study & Learn** — Upload notes (PDF, DOCX, TXT, MD, CSV) or paste text; AI generates
  flashcards, key concepts, quizzes, and summaries. Includes a spaced-repetition system (SRS).
- **🎯 Priority View** — Smart High/Medium/Low scoring with time estimates based on points,
  due-date proximity, and course weight.
- **🏫 Classes View** — Browse/filter assignments by course across all linked platforms.
- **📊 Grades & Grade Modeler** — Live GPA overview plus "what if I get X% on my next test?"
  simulation.
- **🤖 Plani — AI Tutor** — A dedicated step-by-step tutor (math, science, history, English,
  CS, languages, economics, test prep) that teaches instead of just giving answers.
- **🔔 Push Notifications** — Deadline reminders, even when the app is closed.
- **🌙 Dark Mode** — Full light/dark theme support.

---

## Integrations

| Integration | What It Does |
|---|---|
| **Canvas LMS** | Auto-imports assignments, due dates, points, course names via REST API |
| **StudentVue** | Auto-imports assignments and missing work via the StudentVue API |
| **Schoology** | Auto-imports assignments via API key + secret |
| **Google Calendar** | One-click export of AI study schedules; OAuth 2.0 with PKCE |
| **Notion** | Two-way task sync via integration token |
| **Stripe** | IntelliPlan Pro billing ($4.99/mo) — *web only; not shown inside the iOS app* |
| **Chrome Extension** | Badge count of pending assignments; injects into Canvas/StudentVue |
| **PWA (Android/iOS)** | Installable as a native-feeling app |

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python, Flask |
| **Database** | PostgreSQL (production via Railway), SQLite (local dev) |
| **ORM** | Flask-SQLAlchemy |
| **Auth** | Flask-Login, Flask-Bcrypt, Google OAuth 2.0 (PKCE), JWT tokens |
| **Sessions** | Flask-Session (SQLAlchemy-backed, survives Railway restarts) |
| **AI** | Groq API (Llama 3.3 70B Versatile; Llama 3.2 11B Vision for image notes) |
| **School APIs** | Canvas LMS REST API, StudentVue API, Schoology API |
| **Calendar** | Google Calendar API v3 |
| **Notes** | Notion API |
| **Payments** | Stripe (web billing) |
| **Push** | Web Push / VAPID (pywebpush) |
| **Error Tracking** | Sentry |
| **Rate Limiting** | Flask-Limiter |
| **Email/SMS** | Resend (or SMTP fallback); SMS via carrier email gateways |
| **Frontend** | Vanilla HTML/CSS/JS, SVG animations, CSS custom properties |
| **Hosting** | Railway |

---

## File / Folder Structure

```
Source-Code/
│
├── App.py                      # Main Flask app — all routes, models, config (the big one)
├── auth_api.py                 # Auth blueprint — JWT token endpoints
├── chatbot_api.py              # Plani chatbot + tutor API blueprint
├── intelliplan_api.py          # Additional API endpoints
├── intelliplan_mcp.py          # MCP server integration
├── google_calendar_helper.py   # Google OAuth + Calendar API helpers
├── notion_helper.py            # Notion API integration helpers
├── studentvue_helper.py        # StudentVue API helpers
├── studentvue_routes.py        # StudentVue routes
├── canvas_helper.py            # Canvas API helpers
├── canvas_oauth.py             # Canvas OAuth flow
├── canvas_routes.py            # Canvas routes
├── schoology_helper.py         # Schoology API helpers
├── vapid.py                    # VAPID key handling for push
├── local_ai_server.py          # Local AI server (dev/optional)
├── requirements.txt            # Python dependencies
├── Procfile                    # Railway/Gunicorn process config
├── .env.example                # Environment variable template (fill to make a real .env)
│
├── Main_Project/
│   ├── TestData.py
│   └── templates/              # Jinja2 HTML templates (~60 pages)
│       ├── base.html           # Base layout with nav + Plani chatbot widget
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
│       └── ...                 # login, register, connect, legal, blog pages, etc.
│
├── static/
│   ├── css/mobile.css          # Stylesheets
│   ├── icons/                  # PWA icons
│   ├── sw.js                   # Service worker (PWA/offline)
│   ├── manifest.json           # PWA manifest
│   ├── robots.txt / sitemap.xml / llms.txt
│
├── extension/                  # Chrome extension source (manifest, popup, background, content)
│   └── Testing/                # Extension test build
│
├── mcp/                        # MCP server code
├── docs/                       # Setup notes (Canvas OAuth, streak system)
├── instance/                   # SQLite DB target dir (empty in this copy)
├── uploads/                    # User upload target dir (empty in this copy)
└── test_intelliplan.py         # End-to-end test suite
```

---

## Roadmap (future ideas)

- AI-powered grade predictions from historical performance
- Schoology full assignment sync
- Native mobile app (React Native)
- Collaborative study groups
- Teacher/parent dashboard view
- More LMS integrations (Blackboard, Google Classroom, PowerSchool)
