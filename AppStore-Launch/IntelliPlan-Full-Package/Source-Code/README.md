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

IntelliPlan pulls your assignments directly from Canvas, StudentVue, or Schoology, scores them by priority, and uses AI to generate a full weekly study schedule tailored to your workload and available hours. It syncs to Google Calendar, integrates with Notion, and includes a built-in AI tutor — all free.

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
Browse and filter all assignments by course. Works across Canvas, StudentVue, and Schoology simultaneously if multiple accounts are linked.

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
| **StudentVue** | Auto-imports assignments and missing work via the StudentVue API |
| **Schoology** | Auto-imports assignments via API key + secret |
| **Google Calendar** | One-click export of AI-generated study schedules; OAuth 2.0 with PKCE |
| **Notion** | Two-way task sync with your Notion databases via integration token |
| **Chrome Extension** | Badge count showing pending assignments; injects into Canvas and StudentVue pages |
| **PWA (Android)** | Installable as a native app via APK or browser prompt |
| **PWA (iOS)** | Installable via Safari Add to Home Screen |

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python, Flask |
| **Database** | PostgreSQL (production via Railway), SQLite (local dev) |
| **ORM** | Flask-SQLAlchemy |
| **Auth** | Flask-Login, Flask-Bcrypt, Google OAuth 2.0 (PKCE), JWT tokens |
| **Sessions** | Flask-Session (SQLAlchemy-backed for persistence across Railway container restarts) |
| **AI — Scheduling & Study** | Groq API (Llama 3.3 70B Versatile) |
| **AI — Vision / Image Notes** | Groq API (Llama 3.2 11B Vision) |
| **AI — Tutor & Chatbot** | Groq API (Llama 3.3 70B Versatile) |
| **School APIs** | Canvas LMS REST API, StudentVue API, Schoology API |
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
- A Groq API key (free at [console.groq.com](https://console.groq.com))
- Canvas API token, StudentVue credentials, or Schoology API key (at least one)

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
GROQ_API_KEY=your-groq-api-key
DATABASE_URL=sqlite:///intelliplan.db

# Google OAuth (optional — needed for Google Calendar)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:3000/oauth2callback

# Push Notifications (optional)
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
VAPID_EMAIL=

# Sentry (optional)
SENTRY_DSN=

# App URL
APP_BASE_URL=http://localhost:3000
```

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

- [ ] AI-powered grade predictions based on historical performance
- [ ] Schoology full assignment sync
- [ ] Mobile app (React Native)
- [ ] Collaborative study groups
- [ ] Teacher/parent dashboard view
- [ ] More LMS integrations (Blackboard, Google Classroom, Powerschool)

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">
  Built by a student, for students. 🎓<br/>
  <a href="https://intelliplan.tech">intelliplan.tech</a> · <a href="https://discord.gg/34FYWhJQMU">Discord</a> · <a href="https://github.com/UAnirudh/IntelliPlan/issues">Report a Bug</a>
</div>
