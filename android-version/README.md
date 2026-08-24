# IntelliPlan Android release

IntelliPlan is an Expo/React Native app and the Android app is built from the
repository's `mobile/` project. This folder is the Play Store handoff packet;
it is intentionally not a second Android source tree that could drift from the
iOS and web builds.

## Build a release bundle

```powershell
cd mobile
npx eas login
npx eas build:configure
npx eas build --platform android --profile production
```

Upload the resulting `.aab` to Google Play Console. Before submission:

- Set the production `EXPO_PUBLIC_API_BASE` to the Railway deployment URL.
- Confirm `android.package` in `mobile/app.json` is unique and owned by IntelliPlan.
- Replace development app icons/splash assets with final branded assets.
- Configure Google OAuth and Canvas redirect URLs for the production domain.
- Complete Play Console Data safety, content rating, privacy policy, and
  account deletion declarations.
- Test login, Canvas, Google Calendar, push notifications, offline queue
  replay, deep links, and Android back navigation on a release build.

The canonical Android configuration remains `mobile/app.json` and `mobile/eas.json`.
