# IntelliPlan — How It Works (Architecture & API)

This explains the moving parts so you can understand the code without reading all of `App.py`.

---

## High-level flow

```
 Student's browser / PWA / Chrome extension
            │
            ▼
   Flask app (App.py)  ──►  Groq AI API (scheduling, tutoring, study generation)
       │   │   │
       │   │   └──►  School APIs (Canvas / StudentVue / Schoology)  — import assignments & grades
       │   └──────►  Google Calendar API  — export schedules
       │            Notion API            — sync tasks
       ▼
   Database (PostgreSQL on Railway, SQLite locally)
```

1. A student signs up / logs in (Flask-Login + Bcrypt, or Google OAuth).
2. They connect a school platform (Canvas/StudentVue/Schoology) in **Settings**.
3. The app pulls their assignments and grades live from that platform's API.
4. Assignments are scored by priority and shown on the **Dashboard**.
5. The student asks the **AI Scheduler** for a plan → Groq generates study blocks → optional
   one-click export to **Google Calendar**.
6. **Study & Learn** turns uploaded notes into flashcards/quizzes; **Plani** tutors them.

---

## Key files (what to read first)

| File | Responsibility |
|---|---|
| **`App.py`** | The heart of the app. Defines the Flask app, all database models, config, and the bulk of the routes (pages + APIs). Start here. It's large (~410 KB) — search by route name or section comments. |
| **`auth_api.py`** | JWT token endpoints used by the Chrome extension and API clients. |
| **`chatbot_api.py`** | The Plani AI assistant + AI tutor endpoints (talks to Groq). |
| **`intelliplan_api.py`** | Additional JSON API endpoints. |
| **`google_calendar_helper.py`** | Google OAuth 2.0 (PKCE) + Calendar API v3 export logic. |
| **`notion_helper.py`** | Notion integration (connect, fetch/sync tasks). |
| **`canvas_helper.py` / `canvas_oauth.py` / `canvas_routes.py`** | Canvas import + OAuth. |
| **`studentvue_helper.py` / `studentvue_routes.py`** | StudentVue import. |
| **`schoology_helper.py`** | Schoology import. |
| **`vapid.py`** | VAPID keys for web-push notifications. |
| **`Main_Project/templates/`** | All HTML pages (Jinja2). `base.html` is the shared layout. |
| **`static/sw.js`** | Service worker — makes it an installable PWA with offline support. |
| **`extension/`** | The Chrome extension (badge count + Canvas/StudentVue injection). |

---

## Data model (conceptual)

The app stores (in `App.py`'s SQLAlchemy models): user accounts, linked school platform
credentials/tokens, manual tasks, dismissed/completed assignments, saved schedules, study
notes + generated flashcards/quizzes with spaced-repetition state, push subscriptions, and
Stripe/billing state for IntelliPlan Pro.

> In this shared copy the databases are empty — they're created fresh on first run.

---

## API overview

### School Data
| Endpoint | Method | Description |
|---|---|---|
| `/live` | GET | Fetch live assignments from the connected platform |
| `/tasks/unified` | GET | All tasks merged (platform + manual + Notion) |
| `/courses` | GET | List of courses |
| `/grades/data` | GET | Grade data per course |
| `/missing/data` | GET | Missing/overdue assignments |

### AI
| Endpoint | Method | Description |
|---|---|---|
| `/generate_schedule` | POST | Generate an AI study schedule from assignments |
| `/api/tutor` | POST | Plani tutor — multi-turn academic tutoring |
| `/api/chatbot` | POST | Plani assistant — IntelliPlan feature help |
| `/study/generate` | POST | Generate flashcards + quiz from notes |
| `/study/evaluate` | POST | AI-evaluate a student's quiz answer |
| `/study/analyze-image` | POST | Extract text/content from an uploaded image |
| `/notes/<id>/summarize` | POST | AI-summarize uploaded course notes |

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

## The Chrome extension

- Shows a **badge count** of pending assignments on the extension icon.
- **Injects into Canvas and StudentVue pages** for quick access.
- Logs in with an IntelliPlan account (talks to the `/extension/*` endpoints above).
- Source is in `Source-Code/extension/`; load it via `chrome://extensions` → Developer Mode →
  Load unpacked (or use one of the packaged `.zip`s).
