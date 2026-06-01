# IntelliPlan — Full Package (START HERE)

Hi! This folder contains **everything** for IntelliPlan — the full source code plus all the
documentation. It was put together so you have the complete picture in one place.

**Your actual job is small:** you're releasing IntelliPlan on the Apple App Store on my
behalf. I'm still the owner — you're just the person with the Apple Developer account + Mac
who can do the upload. You do **not** need to run the code or touch the server to do that.

---

## 📌 The 10-second version

IntelliPlan is a live website / PWA at **https://intelliplan.tech** (hosted on Railway).
For the App Store, we **wrap the live site** into a small iOS app using a free tool
(PWABuilder), open it in Xcode, and upload it. No app source code is needed for the release.

➡️ **For the release, go straight to [`Documentation/APP-STORE-RELEASE-GUIDE.md`](Documentation/APP-STORE-RELEASE-GUIDE.md).**
The app icon you'll need is right here: **`IntelliPlan-AppIcon-1024.png`**.

---

## 📂 What's in this package

| Folder / File | What it is |
|---|---|
| **`START-HERE.md`** | This file. |
| **`IntelliPlan-AppIcon-1024.png`** | The 1024×1024 app icon for the App Store. |
| **`Source-Code/`** | The complete IntelliPlan codebase (Flask web app + Chrome extension + PWA). Secrets and user data removed — safe copy. |
| **`Documentation/`** | All the written guides (see below). |

### Documentation index

| Doc | Read it if you want to… |
|---|---|
| [`APP-STORE-RELEASE-GUIDE.md`](Documentation/APP-STORE-RELEASE-GUIDE.md) | **← The main task.** Step-by-step App Store submission, the listing text, screenshots, and privacy answers. |
| [`PROJECT-OVERVIEW.md`](Documentation/PROJECT-OVERVIEW.md) | Understand what IntelliPlan is, its features, tech stack, and file structure. |
| [`HOW-IT-WORKS.md`](Documentation/HOW-IT-WORKS.md) | Understand the architecture, the key files, and the API endpoints. |
| [`RUN-LOCALLY.md`](Documentation/RUN-LOCALLY.md) | Run the app on your own machine (optional — not needed for the App Store release). |
| [`DEPLOYMENT-AND-HOSTING.md`](Documentation/DEPLOYMENT-AND-HOSTING.md) | How it's hosted on Railway and how the live site stays running. |
| [`ENVIRONMENT-VARIABLES.md`](Documentation/ENVIRONMENT-VARIABLES.md) | Every config/secret the app uses and where to get each one. |

---

## ⚠️ Important: what was removed (and why)

To keep this safe to share, the following were **intentionally left out** of `Source-Code/`:

- **`.env`** — the real API keys and secrets. A blank template (`.env.example`) is included instead.
- **All `.db` database files** — they contained real user accounts, emails, and password hashes.
- **`uploads/`** — real student notes that users uploaded.

None of these are needed to release the app or to read the code. If you ever actually need to
*run* the backend, ask me for the secrets directly (never put them in a shared file).

---

## ❓ Questions for me

- The one decision to confirm before you submit: it goes live under **your** Apple account to
  get it published, and I keep ownership of the project. Long-term I can move it to my own
  Apple Developer account. Details are in the release guide.
- Anything unclear — just message me. Thank you! 🙏
