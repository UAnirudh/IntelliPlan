# IntelliPlan — Deployment & Hosting

## TL;DR for the App Store release

**Nothing about hosting changes for the App Store.** The live site
(**https://intelliplan.tech**) stays on **Railway** exactly as it is. The iOS app is just a
wrapper that loads that live site. You don't deploy anything, touch the server, or change
Railway to publish on the App Store.

---

## Where it runs now

- **Host:** [Railway](https://railway.app)
- **Process:** Gunicorn, configured in `Procfile`:
  ```
  web: gunicorn App:app --bind 0.0.0.0:$PORT --workers 4 --timeout 120 --max-requests 500 --max-requests-jitter 50
  ```
- **Database:** PostgreSQL (managed by Railway). `DATABASE_URL` is set as a Railway
  environment variable. Locally it falls back to SQLite.
- **Sessions:** Flask-Session stored in the database, so logins survive container restarts.
- **Domain:** `intelliplan.tech` points at the Railway service.
- **Secrets:** all keys (Groq, Google, Stripe, Notion, VAPID, Sentry, etc.) are set as
  **environment variables in the Railway dashboard** — never committed to the repo.

## How a deploy happens

Railway redeploys automatically when new commits are pushed to the connected GitHub repo
(`UAnirudh/IntelliPlan`). The build installs `requirements.txt` and starts the `Procfile`
`web` process.

## How the App Store app relates to all this

```
   iOS app (App Store)  ──loads──►  https://intelliplan.tech  ──►  Railway (Flask + Postgres)
        (a thin wrapper)              (the real product)            (unchanged)
```

- The iOS wrapper is generated from the live URL with **PWABuilder** and uploaded via Xcode.
- Because it points at the live site, every backend update you push to Railway shows up in the
  iOS app automatically — no App Store re-submission needed for content/feature changes.
- You'd only re-submit to Apple if the *wrapper itself* changes (icon, name, version bump).

➡️ Full submission steps: [`APP-STORE-RELEASE-GUIDE.md`](APP-STORE-RELEASE-GUIDE.md)

## If hosting ever needs to move

The app is portable — it's a standard Flask + Postgres app. It can run on Render, Fly.io,
Heroku, a VPS, etc. Just set the same environment variables (see
[`ENVIRONMENT-VARIABLES.md`](ENVIRONMENT-VARIABLES.md)) and run the `Procfile` command. As long
as `intelliplan.tech` keeps resolving to wherever it's hosted, the App Store app keeps working
without any change.
