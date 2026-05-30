# 03 — Privacy & Compliance (the stuff that gets apps rejected)

Three things decide whether IntelliPlan passes review: the **App Privacy form**, the
**"is it a real app" rule (4.2)**, and **payments (3.1.1)**. All three are handled below.

---

## A. App Privacy form (App Store Connect → App Privacy)

Apple asks what data the app collects. Answer honestly with the following — these reflect
what IntelliPlan actually does (accounts, school data, notes, crash logs). **There is no
advertising or third-party tracking SDK in the app**, so you can declare *"Data is not
used to track you."*

**Does the app collect data?** → **Yes.**

| Data type | Collected? | Linked to user? | Used for tracking? | Purpose |
|---|---|---|---|---|
| **Email Address** (Contact Info) | Yes | Yes | No | App Functionality (your account/login) |
| **User ID** (Identifiers) | Yes | Yes | No | App Functionality |
| **Other User Content** (your notes, tasks, assignments) | Yes | Yes | No | App Functionality |
| **Crash Data / Diagnostics** | Yes | Yes | No | App Functionality (error monitoring) |

- When asked **"Is this data used to track you?"** for every item above → **No**.
- So the final summary should be **"Data Linked to You"** (the items above), and
  **NOT** "Data Used to Track You."
- **Privacy Policy URL:** `https://intelliplan.tech/legal` (or `/privacy`). Required.

> If you later add analytics or ads, you must update this form.

---

## B. Guideline 4.2 — "minimum functionality" (the #1 risk for web-wrapped apps)

Apple rejects apps that are *just a website in a shell*. IntelliPlan is **not** — make sure
the reviewer sees that.

**What to do:**
1. **Give the reviewer a working test account.** In App Store Connect →
   **App Review Information**, fill in:
   - **Sign-in required:** Yes
   - **Username / Password:** a real IntelliPlan login that already has a school account
     connected (so grades/assignments actually show — an empty account looks like a
     blank website and gets rejected).
   - **Notes:** paste the text below.

   **Reviewer notes (copy-paste):**
   ```
   IntelliPlan is an AI study-planning app for students. After signing in with the test
   account provided, you'll see imported assignments on the Dashboard, an AI-generated
   study schedule under Scheduler, the Plani AI tutor, grade tracking, and a study/
   flashcard generator. The app connects to Canvas, StudentVue, and Schoology to import
   real coursework and uses AI to prioritize and schedule it — functionality that goes
   well beyond a website, including offline access and reminders.
   ```
2. Make sure the app **opens straight into app-like features**, not a marketing landing
   page. (PWABuilder uses the manifest `start_url`, which is `/dashboard` — good.)
3. If it still gets rejected under 4.2, switch to a **Capacitor** wrapper (more native
   APIs). Steps are at the bottom of this file.

---

## C. Guideline 3.1.1 — Payments (Stripe vs Apple)

The project has a Stripe webhook and a pricing page. **Important rule:**

- If the iOS app lets users **buy a digital subscription/feature**, Apple **requires their
  In-App Purchase** (15–30% cut). **A Stripe checkout for digital goods inside the app =
  automatic rejection.**

**Recommended for v1 (simplest, passes review):**
- Ship the iOS app **Free**, with **no in-app purchasing and no Stripe checkout** shown
  inside the app. If a user is already subscribed via the website, they just sign in and
  use it. Don't add buttons inside the app that link out to a paid web checkout for
  digital features.
- Set **Pricing = Free** in App Store Connect.

**If you must sell subscriptions in the iOS app later:** implement Apple In-App Purchase,
sign the **Paid Applications agreement**, and create the products in App Store Connect.
That's a bigger project — skip it for the first release.

> ✅ Action: before uploading, confirm the iOS build does **not** surface a Stripe/paywall
> screen. If `intelliplan.tech` shows a paywall to logged-out users, consider pointing the
> wrapper's start at the dashboard/login (already the case) and keeping purchases web-only.

---

## D. Permissions / notifications

- **Push notifications inside a web wrapper are limited on iOS.** For the **first release,
  don't depend on push** — it avoids extra APNs setup and a common source of bugs. The app
  works fully without it. (You can add native push later via Capacitor.)
- If Xcode/PWABuilder added any permission usage strings (camera, photos) that the app
  doesn't actually use, you can leave them; if the app does use them, make sure the
  description text is a clear, honest sentence or Apple will reject it.

---

## E. Common rejection causes & fixes

| Symptom | Fix |
|---|---|
| "App is just a web view / 4.2" | Provide the test account + reviewer notes above; ensure real data shows after login. |
| Rejected for payments / 3.1.1 | Remove any Stripe/paywall purchase flow from the iOS app, or use Apple IAP. |
| Crash on launch during review | Usually a missing permission usage string in Info.plist for something the app requests. |
| "Privacy Policy URL missing/invalid" | Make sure `https://intelliplan.tech/legal` loads a real privacy policy. |
| Blank screen after login for reviewer | The test account has no school connected — connect one before submitting. |
| Age rating mismatch | Answer the questionnaire honestly (the AI tutor/chat may push it to 9+/12+). |

---

## Appendix — Capacitor fallback (only if 4.2 rejection)

If PWABuilder's wrapper gets rejected as "too web-like," Capacitor gives real native APIs:

```bash
# on the Mac, in an empty folder
npm init -y
npm i @capacitor/core @capacitor/cli @capacitor/ios
npx cap init IntelliPlan tech.intelliplan.app --web-dir=www
mkdir www && echo "redirecting..." > www/index.html
# point the app at the live site in capacitor.config.json:
#   "server": { "url": "https://intelliplan.tech" }
npx cap add ios
npx cap open ios   # opens Xcode — then archive & upload as in file 01
```

Add native plugins (e.g. `@capacitor/push-notifications`, `@capacitor/share`) to make it
clearly more than a website.
