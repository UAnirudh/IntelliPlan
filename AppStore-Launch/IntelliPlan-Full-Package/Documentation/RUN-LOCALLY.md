# IntelliPlan — Run It Locally (Optional)

> **You do NOT need this to release the app on the App Store.** The App Store version wraps
> the *live* site (https://intelliplan.tech). This is only here in case you want to run the
> code on your own machine to understand it or test changes.

To actually run the backend you'd need the real secret keys, which are **not** included in
this package (for safety). Ask the owner for them if you genuinely need to run it.

---

## Prerequisites

- **Python 3.11+**
- A **Groq API key** (free at https://console.groq.com) — required for any AI feature
- At least one school API credential (Canvas token / StudentVue login / Schoology key) if you
  want to test the import features

## Steps

```bash
# 1. Open a terminal in the Source-Code folder
cd Source-Code

# 2. (Recommended) create a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env from the template
#   Windows:   copy .env.example .env
#   macOS/Linux: cp .env.example .env
# Then open .env and fill in real values (at minimum SECRET_KEY and GROQ_API_KEY).
# See ENVIRONMENT-VARIABLES.md for what each one is and where to get it.

# 5. Run the app
python App.py
```

By default the app serves on **http://localhost:3000**.

With `DATABASE_URL=sqlite:///intelliplan.db` (the default in `.env.example`), a local SQLite
database is created automatically on first run inside `instance/`. No PostgreSQL needed for
local dev.

## Running the tests

```bash
pip install pytest
pytest test_intelliplan.py
```

## Production-style run (optional)

Mirrors how Railway runs it (see `Procfile`):

```bash
gunicorn App:app --bind 0.0.0.0:3000 --workers 4 --timeout 120
```

## Common issues

| Symptom | Likely cause / fix |
|---|---|
| AI features return errors | `GROQ_API_KEY` missing or invalid in `.env` |
| "no such table" on first request | DB not initialized — restart; tables are created on startup |
| Google Calendar export fails | `GOOGLE_CLIENT_ID/SECRET` not set, or redirect URI mismatch |
| Push notifications don't send | `VAPID_*` keys not set (generate with `vapid.py`) |
| Port already in use | Change `PORT` in `.env` or free port 3000 |
