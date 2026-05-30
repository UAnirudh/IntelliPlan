# 04 — Final Checklist

Tick these off in order. Details for each are in files 01–03.

## Before you start
- [ ] Mac with **Xcode** installed and signed in with the Apple Developer Apple ID
- [ ] Confirmed **Bundle ID** = `tech.intelliplan.app`
- [ ] Confirmed **who owns the app** (whose Apple account it's submitted under) — file 01
- [ ] A working **IntelliPlan test login** that has a school account connected (for Apple)

## Build (file 01)
- [ ] Generated the iOS package on **pwabuilder.com** from `https://intelliplan.tech`
- [ ] Opened in Xcode; set **Team**, **automatic signing**, Bundle ID, Display Name, Version 1.0.0 / Build 1
- [ ] Set app icon from `assets/AppIcon-1024.png`
- [ ] Destination = **Any iOS Device (arm64)** → **Product → Archive** → **Upload**
- [ ] Build shows in App Store Connect (waited for "Processing" to finish)

## Listing (file 02)
- [ ] Created the app in App Store Connect (`tech.intelliplan.app`)
- [ ] Pasted Name, Subtitle, Promo text, Keywords, Description, What's New
- [ ] Set Support / Marketing / **Privacy Policy** URLs
- [ ] Set Categories (Education / Productivity) and **Pricing = Free**
- [ ] Selected the uploaded build for the version

## Screenshots & icon (assets/)
- [ ] Captured 3–5 **iPhone 6.9"** screenshots and uploaded them
- [ ] Uploaded the 1024 icon if prompted

## Privacy & compliance (file 03)
- [ ] Completed **App Privacy** form (Data Linked to You; **not** used to track)
- [ ] **No Stripe/paywall purchase** flow inside the iOS app (or proper Apple IAP) — 3.1.1
- [ ] Added **test account + reviewer notes** in App Review Information — 4.2
- [ ] Age rating questionnaire answered honestly

## Ship
- [ ] Installed via **TestFlight** and confirmed it works on a real iPhone
- [ ] **Submitted for Review**
- [ ] (After approval) chose manual or automatic release 🎉
