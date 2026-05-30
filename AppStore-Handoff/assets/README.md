# assets — icon & screenshots

## ✅ App icon (already done)
`AppIcon-1024.png` — 1024×1024, opaque (no transparency), ready to drop into Xcode
(Assets.xcassets → AppIcon → 1024pt slot) and to upload in App Store Connect.

> It was generated from the site's 512px icon. It's fine for launch. For maximum
> crispness you can later replace it with a 1024px export from the original logo, but it
> is **not** required to ship.

---

## 📸 Screenshots (you capture these on the Mac — ~10 min)

Apple requires at least one iPhone screenshot set. The easiest qualifying size is the
**6.9" iPhone** display.

### How to capture
1. In **Xcode**, set the run destination to **iPhone 16 Pro Max** (a 6.9" simulator).
2. **Product → Run** to launch IntelliPlan in the Simulator.
3. Sign in (use the same test account you'll give Apple) so real content shows.
4. Navigate to a nice screen, then save a screenshot:
   - In the Simulator menu: **File → Save Screen** (or press **⌘S**). It saves to your
     Desktop at the correct resolution automatically.
5. Capture **3–5 screens** that sell the app, e.g.:
   - Dashboard (assignments board)
   - AI Scheduler (a generated plan)
   - Plani AI tutor
   - Grades / Grade Modeler
   - Study & flashcards

### Where to upload
App Store Connect → your app → the version → **Previews and Screenshots** →
**iPhone 6.9" Display** → drag your PNGs in. Apple shows these to users.

### Sizes (reference)
| Display | Device to use | Resolution (portrait) |
|---|---|---|
| **6.9"** (required, easiest) | iPhone 16 Pro Max | 1320 × 2868 |
| 6.7" (also accepted) | iPhone 15 Pro Max | 1290 × 2796 |
| iPad 13" (only if app supports iPad) | iPad Pro 13" | 2064 × 2752 |

> If you submit an iPhone-only app, you don't need iPad screenshots. If the app installs
> on iPad too, either add iPad screenshots or set the target to iPhone-only in Xcode
> (General → Supported Destinations).

> Apple occasionally updates accepted sizes — if App Store Connect asks for a size not
> listed here, follow what it shows.
