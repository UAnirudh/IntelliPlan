# IntelliPlan Mobile (React Native / Expo)

A thin React Native client that talks to the existing IntelliPlan Flask
API. Built with Expo so you can run it on iOS, Android, and web from one
codebase.

## Quick start

```bash
cd mobile
npm install
npx expo start
```

Then open the Expo Go app on your phone and scan the QR code, or press
`i` for iOS simulator / `a` for Android emulator.

## What's wired up

- Auth: email + password against `/api/v1/auth/token` (existing endpoint).
- Token storage: `expo-secure-store` — your bearer never touches AsyncStorage.
- Three screens (file-based routing via `expo-router`):
  - `app/login.tsx` — email + password.
  - `app/(tabs)/today.tsx` — pulls `/api/today`.
  - `app/(tabs)/predictions.tsx` — pulls `/api/grade-predictions`.

## Configuration

Point at your dev server by editing `mobile/lib/config.ts`:

```ts
export const API_BASE = 'http://10.0.2.2:5000';   // Android emulator
// export const API_BASE = 'http://localhost:5000'; // iOS simulator
// export const API_BASE = 'https://intelliplan.tech'; // production
```

## Why Expo

A bare React Native app would force native build tooling on day one.
Expo's managed workflow runs the same Flask API client across all three
targets with zero Xcode/Android Studio setup, and you can eject later
when you need a native module.

## Next

- Add push notifications (`expo-notifications` → Flask `/api/push/register`).
- Wire the existing Plani pet animation as a Lottie or Reanimated 3 component.
- Background fetch for streak nudges.
