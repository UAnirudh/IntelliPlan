# Play Store submission checklist

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
- **done** — Android permissions are declared explicitly in `mobile/app.json`
  rather than inherited from plugins: `POST_NOTIFICATIONS`, `CAMERA` and
  `READ_MEDIA_IMAGES` are requested; `RECORD_AUDIO`, `READ_MEDIA_VIDEO` and
  the legacy external-storage pair are blocked. Snap & Solve takes photos
  only, so the video and audio permissions would have been unused
  permissions on the listing — which is both a review risk and a Data Safety
  mismatch.
- **done** — Feature graphic at `mobile/assets/play-feature-graphic.png`
  (1024×500), regenerable with `python3 scripts/gen-feature-graphic.py`. It
  is a clean brand placeholder, not a designed asset; replace it when there
  is one.
- **done** — `npx tsc --noEmit` passes; `npx expo-doctor` passes 16/18, with
  the two failures being network reachability from the sandbox rather than
  project problems.
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

## Store listing — you

- [ ] App name, short and full descriptions — drafts in `STORE-LISTING.md`.
- [ ] Icon (have) and feature graphic (have, placeholder).
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
