# Play Store submission checklist

The current release gate checklist is [`Google-Play-Launch/README.md`](../Google-Play-Launch/README.md).
This older packet is retained for its verified account-deletion details and
task notes; use the linked guide for current Play Console, build, privacy,
testing, and release sign-off steps.

Status as of the last pass through this repo. Anything marked **done** was
checked against the code, not assumed; anything marked **you** needs a person
with Play Console access, a physical device, or a design tool.

## Verified in this repo

- **done** — Account deletion works end to end. `POST /account/delete`
  authenticates by session *or* by the mobile bearer token, and now clears
  every table carrying a `users.id` foreign key. It previously listed 29 of
  58 such tables and filtered two of them on columns that do not exist
  (`day_archive` for `day_archives`, and `live_sessions.user_id` for a
  column named `owner_id`), so under Postgres the final `DELETE FROM users`
  raised and the endpoint returned a 500. `tests/test_account_deletion.py`
  walks the live model registry and fails if a new model reintroduces the
  gap.
- **done** — Public data-deletion URL: `https://intelliplan.tech/delete-account`
  (also `/account-deletion`). No sign-in, no app install. This is the URL for
  the Play Console **Data deletion** field.
- **done** — In-app deletion route: Settings → Delete account, with a
  confirm step, in `mobile/app/settings.tsx`.
- **done** — Privacy policy URL: `https://intelliplan.tech/legal#privacy`
  (`/privacy` 301-redirects there).
- **checked in config** — `mobile/app.json` explicitly declares
  `POST_NOTIFICATIONS` and `CAMERA`, and blocks `RECORD_AUDIO`,
  `READ_MEDIA_VIDEO` and the legacy external-storage pair. Expo plugins can
  contribute permissions to the merged Android manifest; inspect the
  generated AAB, especially the image-picker permissions, before answering
  Play's permissions and Data Safety forms.
- **done** — Designed feature graphic at `mobile/assets/play-feature-graphic.png`
  (1024×500), regenerable from `mobile/` with
  `python scripts/gen-feature-graphic.py`. It uses the flat light/blue visual
  system and an illustrative Today timeline; it avoids gradient fills and
  keeps essential copy away from the store badge/crop edges.
- **done** — `npm run typecheck` passes; `npx expo-doctor` passes all 21
  checks after aligning the Expo SDK 57 package patch versions.
- **done** — Release config: `eas.json` production profile builds an
  `.aab`, `appVersionSource: "remote"` with `autoIncrement` so version codes
  cannot collide, and the submit profile targets the internal track as a
  draft.

## Play Console — you

- [ ] Create/select the Google Play Developer account.
- [ ] Create the app with package name `tech.intelliplan.app`.
- [ ] Complete developer/app-signing agreements and contact details.
- [ ] Complete the closed-test period if the account is subject to it
      (personal accounts created after Nov 2023: 12 testers, 14 days).

## Build and signing — you

- [ ] `npx eas login` and `npx eas project:init` from `mobile/` if needed.
- [ ] `npx eas credentials --platform android` to configure app signing.
- [ ] Back up the keystore somewhere durable; never commit it. Losing it
      means never updating this listing again.
- [ ] Upload the Play service-account JSON to EAS credentials.
- [ ] `npx eas build --platform android --profile production`.
- [ ] Install the release build on a physical device and check it against
      production, not just the emulator against localhost.

Current local state noted in this packet: `mobile/extra.eas.projectId` is not
linked, EAS CLI is not authenticated in this checkout, and no production
build has been created. A fresh `npm audit` in `mobile/` on 2026-10-27
reported 7 low and 0 moderate/high/critical findings in the Expo CLI/build
dependency tree; re-run it before release. Do not use `npm audit fix --force`
without reviewing its proposed Expo SDK downgrade.

## Store listing — you

- [ ] App name, short and full descriptions — drafts in `STORE-LISTING.md`.
- [ ] Icon and feature graphic (design complete; verify the final upload in
      Play Console's listing preview).
- [ ] **Phone screenshots — at least 2, and the real blocker here.** Nothing
      in this repo can produce them; they need the release build running on
      a device or emulator. Suggested set: Today, Plan, Plani, Grades.
- [ ] Tablet screenshots — `ios.supportsTablet` is on and the layouts are
      responsive, so either supply them or say the app is phone-only.
- [ ] Support email and website.
- [ ] Privacy policy URL (above).
- [ ] Reviewer test-account credentials. The app is useless behind a login,
      so a reviewer without one will bounce it.

## App content and policy — you

- [ ] Data Safety form — start from `DATA-SAFETY.md`, verify each answer
      against what production actually does.
- [ ] Data deletion form — use the URL above.
- [ ] Content rating questionnaire.
- [ ] Target audience and content. **This one deserves care:** IntelliPlan
      is for students, collects a birth year, and gates under-13 accounts on
      parental consent. Declaring a child-inclusive target audience pulls the
      app into the Families policy programme, which brings its own
      requirements for ads, SDKs and content. Answer it deliberately, not
      quickly.
- [ ] Ads declaration (the app serves none), financial features, and health
      declarations.
- [ ] Permission justifications for camera, photos, and notifications.

## Release testing — you

- [ ] Sign up, sign in, sign out, and **delete an account** on the release
      build against production. That last one is the path this pass fixed;
      confirm it returns 200 and the account is really gone.
- [ ] Connect Canvas and Google Calendar through the production browser flows.
- [ ] Create a task, generate a plan, complete a task, run a focus session.
- [ ] Snap & Solve with both camera and library, and check the permission
      denial path reads sensibly.
- [ ] Offline queue replay, push notifications, deep links, back navigation,
      keyboard behaviour, and dark mode.
- [ ] Upload to Internal testing, clear the pre-launch report, then promote.

Official references:

- https://support.google.com/googleplay/android-developer/answer/9859152
- https://support.google.com/googleplay/android-developer/answer/10787469
- https://support.google.com/googleplay/android-developer/answer/13327111
- https://docs.expo.dev/submit/android/
