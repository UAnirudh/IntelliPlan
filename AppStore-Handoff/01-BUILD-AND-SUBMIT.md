# 01 — Build & Submit IntelliPlan (full walkthrough)

Follow these steps top to bottom on your **Mac**. Everything you need is in this folder.

- **Live site to wrap:** https://intelliplan.tech
- **Recommended Bundle ID:** `tech.intelliplan.app`
- **App name:** IntelliPlan

---

## Step 0 — Install the tools (one time)

1. Install **Xcode** from the Mac App Store (large download).
2. Open Xcode once and let it finish installing components. Accept the license.
3. (Optional, only if Xcode asks) install command line tools:
   `xcode-select --install`

---

## Step 1 — Generate the iOS app from the website (PWABuilder)

PWABuilder is a free Microsoft tool that turns a PWA into a native app package.

1. Go to **https://www.pwabuilder.com**.
2. Enter **`https://intelliplan.tech`** and click **Start**.
3. It analyzes the site. If it flags manifest/icon warnings, they're usually non-blocking —
   you can continue. (IntelliPlan already has a valid manifest and a 512px icon.)
4. Click **Package For Stores → iOS**.
5. In the iOS options, set:
   - **Bundle ID:** `tech.intelliplan.app`
   - **App name:** `IntelliPlan`
   - **URL:** `https://intelliplan.tech`
   - Leave other fields at defaults unless you know otherwise.
6. Click **Download**. You get a `.zip` containing an **Xcode project**.
7. Unzip it somewhere easy to find (e.g. `~/Desktop/IntelliPlan-iOS`).

> 💡 PWABuilder's package is a WKWebView wrapper around the live site, with offline support
> and push hooks. Because IntelliPlan has real app features (school-data sync, AI tutor,
> notifications), it qualifies as a real app — but read `03-PRIVACY-AND-COMPLIANCE.md` for
> how to avoid the "it's just a website" rejection.

---

## Step 2 — Open & configure in Xcode

1. In the unzipped folder, open the **`.xcworkspace`** file (if there's a `.xcworkspace`,
   always use that over `.xcodeproj`). It opens in Xcode.
2. In the left sidebar click the top **project** icon, then select the app **target**.
3. Go to the **Signing & Capabilities** tab:
   - ✅ Check **Automatically manage signing**.
   - **Team:** select your Apple Developer team from the dropdown (sign in with your Apple
     ID under Xcode → Settings → Accounts if it's empty).
   - **Bundle Identifier:** confirm it's `tech.intelliplan.app`.
4. In the **General** tab:
   - **Display Name:** `IntelliPlan`
   - **Version:** `1.0.0`  •  **Build:** `1`

### Set the app icon
1. In the sidebar, open **Assets.xcassets → AppIcon**.
2. Drag **`assets/AppIcon-1024.png`** (from this folder) onto the **1024pt App Store** slot.
   Modern Xcode only needs the single 1024 image — it generates the rest.

---

## Step 3 — Archive & upload

1. At the top of Xcode, set the run destination to **"Any iOS Device (arm64)"**
   (you cannot archive while a simulator is selected).
2. Menu: **Product → Archive**. Wait for the build to finish.
3. The **Organizer** window opens. Select the new archive →
   **Distribute App → App Store Connect → Upload**.
4. Accept the automatic signing prompts and let it upload.
5. The build will show as **"Processing"** in App Store Connect for ~5–30 minutes.

> If Archive is greyed out, the destination is still a simulator — switch it to
> "Any iOS Device (arm64)".

---

## Step 4 — Create the app in App Store Connect

Open **https://appstoreconnect.apple.com** (sign in with the same Apple ID).

1. **My Apps → ➕ → New App**:
   - Platform: **iOS**
   - Name: **IntelliPlan**
   - Primary Language: **English (U.S.)**
   - Bundle ID: select **`tech.intelliplan.app`**
   - SKU: `intelliplan-ios-001` (any internal id)
   - Full Access
2. You'll land on the app page. Now fill in the listing using **`02-APP-STORE-LISTING.md`**
   (copy-paste each field).
3. Upload **screenshots** — see **`assets/README.md`** for the required sizes and how to
   capture them from the iOS Simulator.
4. Upload the **app icon** if asked (use `assets/AppIcon-1024.png`).
5. Complete the **App Privacy** section using **`03-PRIVACY-AND-COMPLIANCE.md`**.
6. Set **Pricing** to **Free** (see the payments note in file 03 — keep the iOS app free).
7. Under the version, **select the build** you uploaded in Step 3 (it appears once
   "Processing" finishes).

---

## Step 5 — Test on TestFlight (recommended)

1. In App Store Connect → your app → **TestFlight** tab.
2. Once the build is processed, install the **TestFlight** app on your iPhone and add
   yourself as a tester (or use "internal testing").
3. Open IntelliPlan on the phone and confirm: it loads, you can log in, the dashboard
   works, and nothing crashes. Fix anything before submitting.

---

## Step 6 — Submit for review

1. App Store Connect → your app → **App Store** tab.
2. Make sure: build selected, all metadata + screenshots in, App Privacy done.
3. In **App Review Information**, add a **test account** (email + password for a working
   IntelliPlan login) and notes — see file 03 for what to write. This is important so the
   reviewer can actually use the app.
4. Click **Add for Review → Submit for Review**.
5. Apple usually responds in **24–48 hours**. On approval, choose to release manually or
   automatically.

🎉 That's it.

---

## Who owns the app?

Whoever's Apple Developer account this is submitted under **owns** the published app —
they control updates, pricing, and any future changes.

- **Submit under your (the friend's) account:** fastest way to get it live. But the owner
  then depends on you for every update.
- **Better long-term:** the owner gets their **own** Apple Developer account ($99/yr), and
  adds you under **Users and Access** as a developer/admin so you can do the technical
  work. The app then lives in the owner's account.
- Apps **can** be transferred between accounts later, but it has conditions — cleaner to
  decide before the first submission.

Confirm this with the app owner before Step 4.

---

## No Mac?

- **Codemagic** (codemagic.io) — Flutter/PWA-friendly CI that can build & upload iOS apps
  from the cloud (free tier).
- **GitHub Actions** with `macos-latest` runners.
- **MacinCloud / MacStadium** — rent a remote Mac by the hour.

All still need your Apple Developer credentials configured in the service.

---

## If something goes wrong

See **`03-PRIVACY-AND-COMPLIANCE.md` → Common rejection causes** and the troubleshooting
notes there. The two most common issues for this kind of app are (1) Apple thinking it's
"just a website" and (2) a payment/paywall problem — both are covered in file 03.
