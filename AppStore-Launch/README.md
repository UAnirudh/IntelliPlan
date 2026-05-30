# IntelliPlan, Apple App Store (everything in one file)

This single file has **everything** needed to publish IntelliPlan to the iOS App Store.

- **Part 1** is a ready-to-send **message to your friend** (the person with the Apple
  Developer account + Mac).
- **Part 2** is the **full step-by-step guide** he follows.
- **Part 3** is the **copy-paste App Store listing** text.
- **Part 4** is the **privacy / compliance** answers.
- **Part 5** is a **one-page checklist**.

> **The one hard requirement:** iOS apps can only be built and uploaded from **macOS with
> Xcode**. An Apple Developer subscription alone is not enough, and it cannot be done on
> Windows. IntelliPlan is a website/PWA (live at **https://intelliplan.tech**), so the job
> is to **wrap the live site into a small iOS app** and submit it, the same idea as the
> Google Play package, just for Apple. No app source code is needed.

The only extra file that goes with this guide is the app icon: **`IntelliPlan-AppIcon-1024.png`**.

---

# Part 1, Message to send your friend

> Copy everything in the box below and send it to him (text/email/Discord), and attach
> this file plus `IntelliPlan-AppIcon-1024.png`.

```
Hey! Thanks for helping me get IntelliPlan onto the App Store with your Apple Developer
account. I've attached a full guide (README.md) and the app icon
(IntelliPlan-AppIcon-1024.png). Here's the short version + the exact details you'll need
to type in.

WHAT IT IS
IntelliPlan is my study-planner web app (live at https://intelliplan.tech). It's a PWA,
so we don't build native code, we wrap the live site into an iOS app using a free tool
called PWABuilder, then you open it in Xcode and upload it. The full guide walks through
every click.

WHAT YOU NEED
- Your Apple Developer account (you have it)
- A Mac with Xcode installed (free from the Mac App Store), required, can't be done on
  Windows
- About 2–3 hours, plus Apple's ~1–2 day review wait

DETAILS TO INPUT (use these exact values)
- Website to wrap:      https://intelliplan.tech
- App name:             IntelliPlan
- Bundle ID:            tech.intelliplan.app   (don't change this once submitted)
- SKU:                  intelliplan-ios-001
- Primary language:     English (U.S.)
- Category:             Education (primary), Productivity (secondary)
- Price:                Free  (important, keep it free, no in-app purchases; see guide)
- Privacy Policy URL:   https://intelliplan.tech/legal
- Support URL:          https://intelliplan.tech/faq
- App icon:             use the attached IntelliPlan-AppIcon-1024.png

THE 5 STEPS (full detail in the guide)
1. PWABuilder.com → enter https://intelliplan.tech → Package for stores → iOS → download
2. Open the downloaded project in Xcode → set Team + Bundle ID, turn on automatic signing
3. Product → Archive → Distribute App → App Store Connect → Upload
4. appstoreconnect.apple.com → create the app, paste the listing text from the guide,
   add screenshots, fill the privacy form
5. Test via TestFlight, then Submit for Review

TWO THINGS THAT CAUSE REJECTIONS (both handled in the guide)
- Apple may say "this is just a website." I've written reviewer notes + you'll add a test
  login so they see real features. I'll send you a working IntelliPlan test account.
- No Stripe/paywall inside the iOS app, keep it free. The guide explains.

One decision for me to confirm: whose Apple account owns the published app. Submitting
under yours is fine to get it live; long-term I may move it to my own account. (Guide has
the details.)

Anything unclear, just ask. Thank you!! 🙏
```

---

# Part 2, Full step-by-step guide (for your friend, on the Mac)

### Step 0, Install tools (one time)
1. Install **Xcode** from the Mac App Store; open it once and let it finish setup; accept
   the license.
2. If prompted: `xcode-select --install`.

### Step 1, Generate the iOS app from the website (PWABuilder)
1. Go to **https://www.pwabuilder.com**.
2. Enter **`https://intelliplan.tech`** → Start. Let it analyze (minor manifest warnings
   are fine to continue past).
3. **Package For Stores → iOS**. Set:
   - **Bundle ID:** `tech.intelliplan.app`
   - **App name:** `IntelliPlan`
   - **URL:** `https://intelliplan.tech`
4. **Download** the `.zip` (an Xcode project). Unzip it somewhere easy, e.g.
   `~/Desktop/IntelliPlan-iOS`.

### Step 2, Configure in Xcode
1. Open the **`.xcworkspace`** (use the workspace, not `.xcodeproj`).
2. Select the app **target → Signing & Capabilities**:
   - ✅ **Automatically manage signing**
   - **Team:** your Apple Developer team (sign in via Xcode → Settings → Accounts if empty)
   - **Bundle Identifier:** confirm `tech.intelliplan.app`
3. **General tab:** Display Name `IntelliPlan`, Version `1.0.0`, Build `1`.
4. **App icon:** open **Assets.xcassets → AppIcon** and drag **`IntelliPlan-AppIcon-1024.png`**
   onto the 1024 slot.

### Step 3, Archive & upload
1. Set the destination to **"Any iOS Device (arm64)"** (can't archive on a simulator).
2. **Product → Archive**.
3. In the Organizer: **Distribute App → App Store Connect → Upload**.
4. Wait ~5–30 min for the build to finish **"Processing"** in App Store Connect.

### Step 4, Create the app in App Store Connect
Go to **https://appstoreconnect.apple.com → My Apps → ➕ → New App**, then use the values
in **Part 3**. Add screenshots (see below) and complete **App Privacy** (**Part 4**).
Set **Pricing = Free**. Select the processed build for the version.

**Screenshots (capture on the Mac, ~10 min):** in Xcode set the simulator to **iPhone 16
Pro Max** (6.9"), **Run** the app, sign in with the test account so real content shows, then
**File → Save Screen (⌘S)** on 3–5 screens (Dashboard, Scheduler, Tutor, Grades). Upload
them under the version's **iPhone 6.9" Display**. iPad screenshots only if the app supports
iPad.

### Step 5, TestFlight, then submit
1. **TestFlight** tab → install on a real iPhone → confirm it loads, logs in, and doesn't
   crash.
2. **App Store** tab → confirm build + metadata + privacy → **Add for Review → Submit**.
3. Review takes ~24–48h. On approval, release manually or automatically.

### Who owns the published app?
Whoever's account it's submitted under **owns** it (controls updates/pricing). Submitting
under your friend's account is fine to launch; for long-term control the owner can get
their own Apple Developer account ($99/yr) and add the friend under **Users and Access**.
Apps can be transferred later but with conditions, decide before first submission.

### No Mac?
**Codemagic** (codemagic.io) or **GitHub Actions** `macos-latest` can build/upload in the
cloud; **MacinCloud/MacStadium** rent a remote Mac. Apple credentials still required.

---

# Part 3, App Store listing (copy-paste, within Apple's limits)

**App Name** (≤30): `IntelliPlan: AI Study Planner`
**Subtitle** (≤30): `Plan smarter with Canvas + AI`
**Bundle ID:** `tech.intelliplan.app`  ·  **SKU:** `intelliplan-ios-001`
**Primary Category:** Education  ·  **Secondary:** Productivity  ·  **Price:** Free

**Promotional Text** (≤170):
```
Connect Canvas, StudentVue, or Schoology and let AI build your study schedule, prioritize assignments, and tutor you, all in one free app for students.
```

**Keywords** (≤100, no spaces after commas):
```
study planner,canvas,studentvue,schoology,homework,assignments,AI tutor,schedule,grades,GPA,student
```

**Description** (≤4000):
```
IntelliPlan is the AI-powered study planner built by a student, for students. Connect your school account and turn a messy list of due dates into a clear, personalized plan, so you always know what to work on next.

Canvas and StudentVue show you WHAT'S due. IntelliPlan tells you WHEN to do it, WHAT to prioritize, and HOW LONG it'll take.

CONNECT YOUR SCHOOL
- Canvas LMS, StudentVue, and Schoology import assignments, due dates, and grades automatically
- Link more than one account at once, or add manual tasks

AI STUDY SCHEDULER
- Tell it your available hours and when you study best
- Get a multi-day plan in focused blocks with breaks
- Export to Google Calendar in one tap

SMART PRIORITIES
- Every assignment scored High / Medium / Low with a time estimate
- A clean board with Overdue, Today, and Upcoming

STUDY & LEARN
- Upload notes (PDF, DOCX, and more) and get flashcards, key concepts, summaries, and quizzes
- Built-in spaced repetition tracks what you've mastered

PLANI, YOUR AI TUTOR
- Step-by-step help in math, science, history, English, CS, languages, and test prep
- Builds understanding instead of just giving answers

GRADES & GRADE MODELER
- See your GPA and simulate "what if I get X% on my next test?"

Made by a student who got tired of falling behind. Questions? Visit https://intelliplan.tech
```

**What's New (v1.0.0):**
```
Welcome to IntelliPlan! Our first App Store release. Connect Canvas, StudentVue, or Schoology, generate AI study schedules, use the Plani AI tutor, and keep all your assignments in one place. Feedback welcome at https://intelliplan.tech
```

**URLs:** Support `https://intelliplan.tech/faq` · Marketing `https://intelliplan.tech` ·
**Privacy Policy (required)** `https://intelliplan.tech/legal`

**Age Rating:** answer honestly; likely **4+**, though the AI tutor/chat may push it to
9+/12+ depending on the questionnaire.

---

# Part 4, Privacy & compliance

### App Privacy form (App Store Connect → App Privacy)
**Does the app collect data? → Yes.** Declare these, all **linked to the user** and **NOT
used for tracking** (IntelliPlan has no ad/tracking SDK):

| Data | Collected | Linked | Tracking | Purpose |
|---|---|---|---|---|
| Email Address | Yes | Yes | No | App Functionality (account) |
| User ID | Yes | Yes | No | App Functionality |
| User Content (notes, tasks) | Yes | Yes | No | App Functionality |
| Crash/Diagnostics | Yes | Yes | No | App Functionality |

Result: **"Data Linked to You"**, **not** "Data Used to Track You."
Privacy Policy URL: `https://intelliplan.tech/legal`.

### Guideline 4.2, "it's just a website" (the #1 risk)
In **App Review Information**, set **Sign-in required: Yes** and provide a **working test
account** that already has a school connected (so real assignments show). Paste these notes:
```
IntelliPlan is an AI study-planning app for students. After signing in with the test
account, you'll see imported assignments on the Dashboard, an AI-generated study schedule
under Scheduler, the Plani AI tutor, grade tracking, and a flashcard/quiz generator. It
connects to Canvas, StudentVue, and Schoology to import real coursework and uses AI to
prioritize and schedule it, well beyond a website, including offline access and reminders.
```
The app opens to `/dashboard` (app-like), not a marketing page, good. If still rejected
under 4.2, switch to a **Capacitor** wrapper for more native APIs.

### Guideline 3.1.1, payments
The project has a Stripe webhook + pricing page. **Apple requires In-App Purchase for
digital goods bought inside the app; a Stripe paywall inside iOS = rejection.** For v1:
ship **Free with no in-app purchasing / no Stripe checkout shown in the app**. Add Apple
IAP later only if you sell subscriptions in-app.

### Notifications
Web-push inside an iOS wrapper is limited, **don't depend on push for v1**; the app works
without it.

### Common rejections → fixes
| Symptom | Fix |
|---|---|
| "Just a web view" (4.2) | Test account + reviewer notes above; ensure real data shows |
| Payments (3.1.1) | No Stripe paywall in the iOS app, or use Apple IAP |
| Crash on launch | Missing Info.plist usage string for a permission requested |
| Privacy URL invalid | Ensure `https://intelliplan.tech/legal` loads |
| Blank after login | Test account has no school connected, connect one first |

---

# Part 5, Checklist

- [ ] Mac + Xcode installed; signed in with the Apple Developer Apple ID
- [ ] Bundle ID `tech.intelliplan.app` confirmed; ownership decided
- [ ] Working IntelliPlan **test login** (school connected) ready for Apple
- [ ] PWABuilder package generated from `https://intelliplan.tech`
- [ ] Xcode: Team + automatic signing set; icon from `IntelliPlan-AppIcon-1024.png`
- [ ] Archived → uploaded → finished "Processing"
- [ ] App record created; listing (Part 3) pasted; **Pricing = Free**
- [ ] 3–5 iPhone 6.9" screenshots uploaded
- [ ] App Privacy form done (Part 4); reviewer notes + test account added
- [ ] No Stripe/paywall inside the iOS build
- [ ] Tested via TestFlight
- [ ] Submitted for review 🎉
