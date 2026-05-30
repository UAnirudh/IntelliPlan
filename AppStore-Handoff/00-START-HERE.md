# 📦 IntelliPlan → Apple App Store — Handoff Pack

Hi! This folder has **everything you need to publish IntelliPlan to the iOS App Store.**
Read this page first, then follow the numbered files in order.

IntelliPlan is a web app (a PWA) that's live at **https://intelliplan.tech**. You're going
to wrap that live site into a small native iOS app and submit it — you do **not** need the
app's source code to do this.

---

## What's in this folder

| File | What it's for |
|---|---|
| **00-START-HERE.md** | This overview (read first) |
| **01-BUILD-AND-SUBMIT.md** | The full step-by-step: generate the app, build in Xcode, upload |
| **02-APP-STORE-LISTING.md** | Copy-paste text for the App Store listing (name, description, keywords…) |
| **03-PRIVACY-AND-COMPLIANCE.md** | Privacy policy URL + answers for Apple's "App Privacy" form + rejection-avoidance |
| **04-CHECKLIST.md** | One-page checklist to tick off before submitting |
| **assets/AppIcon-1024.png** | The 1024×1024 app icon, ready to use |
| **assets/README.md** | Which screenshots Apple needs + how to capture them |

---

## What you (the friend) need

- ✅ An **Apple Developer Program** account (you have this).
- ✅ A **Mac** with **Xcode** installed (free from the Mac App Store). **This is required —
  iOS apps cannot be built or uploaded from Windows.** No Mac? See the end of
  `01-BUILD-AND-SUBMIT.md` for cloud-Mac options.
- ⏱ About **2–3 hours** the first time, plus Apple's 24–48h review wait.

---

## ⚠️ One decision the app owner must confirm before you start

**Bundle ID + who owns the app.** The recommended Bundle ID is:

> ### `tech.intelliplan.app`

This is permanent once submitted. Whoever's Apple Developer account you submit under will
**own** the published app (control updates, etc.). If the plan is for the owner to keep
control long-term, they should publish under *their own* account and add you as a developer
— but submitting under your account is fine to get it live. (Details in
`01-BUILD-AND-SUBMIT.md` → "Who owns the app".)

---

## The 5-minute mental model

1. **PWABuilder.com** turns the live site (intelliplan.tech) into an Xcode project. *(5 min)*
2. **Xcode** on your Mac: set the Bundle ID + your signing Team, then Archive & Upload. *(30 min)*
3. **App Store Connect**: create the app, paste in the listing text (file 02), upload the
   icon + screenshots, fill the privacy form (file 03). *(45 min)*
4. **TestFlight**: install on your iPhone, make sure it works. *(15 min)*
5. **Submit for review.** Apple replies in ~1–2 days. *(done!)*

That's it. Open **01-BUILD-AND-SUBMIT.md** and go step by step. 🚀
