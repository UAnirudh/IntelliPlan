# IntelliPlan Android / Google Play release packet

IntelliPlan is an Expo/React Native app and the Android app is built from the
repository's `mobile/` project. This folder is the Play Store handoff packet;
it is intentionally not a second Android source tree that could drift from the
iOS and web builds.

## What is already configured

- Android application id: `tech.intelliplan.app`
- Store version: `1.0.0`
- Production builds: Android App Bundle (`.aab`)
- EAS remote versioning with automatic production increments
- Production API: `https://intelliplan.tech`
- Adaptive icon, splash screen, notification icon, camera/photo permissions,
  secure storage, Expo Router, and Android notification permission
- In-app account deletion at `/account/delete`, linked from mobile Settings

The source of truth is `../mobile/`. This folder contains release instructions,
store-copy templates, and policy checklists; it is not a duplicate Android app.

## Build a release bundle

```powershell
cd mobile
npx eas login
npx eas build:configure
npx eas build --platform android --profile production
```

Before the first submission, complete [PLAY-STORE-CHECKLIST.md](PLAY-STORE-CHECKLIST.md),
[STORE-LISTING.md](STORE-LISTING.md), and [DATA-SAFETY.md](DATA-SAFETY.md).

Upload the resulting `.aab` to Google Play Console, or submit the draft release
through EAS:

```powershell
npx eas submit --platform android --profile production
```

EAS Submit requires a Google Play service-account key uploaded to the EAS
project. Never commit that JSON key or an `EXPO_TOKEN`.

## Important limitations

- Play Console developer identity, app creation, store listing, tester access,
  Data Safety answers, content declarations, and final publication require the
  account owner and cannot be automated from this repository.
- Google Play may require a closed test before production for newer personal
  developer accounts; follow the requirement shown in Play Console.
- Verify `https://intelliplan.tech/legal#privacy` and account deletion are
  publicly reachable before submitting.

The canonical Android configuration remains `mobile/app.json` and `mobile/eas.json`.
