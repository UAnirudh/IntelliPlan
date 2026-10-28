# IntelliPlan Google Play release checklist

This is the owner-facing checklist for releasing the native Expo app in
`mobile/` on Google Play. The repository can prepare and verify the app bundle,
but Play Console declarations, account credentials, legal decisions, device
testing, and publication need the owner or an authorized reviewer.

Reviewed against the checked-in configuration on 2026-10-27. Recheck Google
Play requirements and the production build before every submission; store
policies and target API deadlines can change.

## Current Android build configuration

| Setting | Checked-in value | Release note |
| --- | --- | --- |
| App | `IntelliPlan` (`mobile/app.json`) | Confirm the final public name in Play Console. |
| Package name | `tech.intelliplan.app` | Use this when creating the Play Console app. Application IDs cannot be changed for an existing Play app. |
| App version | `1.0.0` | Review the marketing version before the first public release. |
| Local Android version code | `1` | Production builds use EAS remote versioning and auto-increment; verify the generated bundle has a new version code. |
| Production artifact | Android App Bundle (`.aab`) | `mobile/eas.json` production profile sets `android.buildType` to `app-bundle`. |
| Production API | `https://intelliplan.tech` | Set in the EAS production profile as `EXPO_PUBLIC_API_BASE`. |
| EAS submit destination | Internal track, draft | The checked-in submit profile does not publish a production release. Choose the intended test/production track in Play Console or update submission configuration deliberately. |
| EAS project link | Not set in `mobile/app.json` (`extra.eas` is empty) | The owner must create/link the EAS project and authenticate before remote builds. |

The Expo SDK is 57 (`mobile/package.json`). `mobile/app.json` declares
`POST_NOTIFICATIONS` and `CAMERA` and blocks several unused audio/video and
legacy storage permissions. Expo plugins can contribute manifest entries, so
review the permissions in the generated AAB/Play Console before submitting;
the checked-in permission array alone is not the merged Android manifest.
In particular, confirm the image-picker permission set matches the released
build and the user-visible photo flow.

Google Play's target API requirement is time-sensitive. As of this review,
new apps and updates submitted from August 31, 2026 must target Android 16
(API 36) or later. Check the target SDK in the generated AAB and the current
[Play target API requirements](https://developer.android.com/google/play/requirements/target-sdk)
before uploading; the Expo SDK number does not itself prove the built target
API level.

After building an AAB, inspect the artifact itself (not just `app.json`) with
the repository audit. It checks package identity, remote version code,
target API, configured and blocked permissions, and prints the complete
merged permission list for owner review. Install Java and the official
`bundletool` release first, then run from `mobile/`:

```powershell
$env:BUNDLETOOL_JAR = "C:\tools\bundletool-all.jar"
npm run audit:android-bundle -- --bundle .\path\to\production.aab
```

The audit defaults to target API 36, currently required for new Play
submissions and updates. `--fail-on-undeclared` makes merged permissions not
listed in `app.json` block the audit; the default prints them as a review item
because Expo and Android libraries may contribute baseline permissions.
This artifact check does not replace Play Console review, Data safety review,
or physical-device testing. See the [Android `bundletool` documentation](https://developer.android.com/tools/bundletool)
for the official artifact inspection utility.

## 1. Owner accounts, app record, and credentials

- [ ] Confirm the legal developer-account owner and the Google Play Developer
      account type. Finish identity, developer profile, distribution, and
      Play App Signing agreements in Play Console.
- [ ] Create the Play app with package `tech.intelliplan.app`. Confirm the
      app type, default language, free/paid choice, and public support email.
- [ ] Create or select the EAS project from `mobile/` with the owner account:

  ```powershell
  cd mobile
  npx eas login
  npx eas init
  ```

- [ ] Review the EAS project ID written to `mobile/app.json` and keep it under
      version control; it is project metadata, not a credential.
- [ ] Configure Android app signing through EAS / Play App Signing. Record
      who controls the signing key and recovery path in the owner's secure
      credential inventory. Never commit a keystore, service-account JSON,
      access token, or `EXPO_TOKEN`.
- [ ] Create a Google Play service account for EAS Submit, grant only the
      Play Console access needed to upload the intended track, and store its
      key in EAS credentials. Do not put the JSON key in this repository.
- [ ] If remote push reminders are part of the release promise, configure the
      Firebase project and FCM V1 credentials in EAS, then validate delivery
      on a physical Android device. The EAS project and Android signing alone
      do not set up FCM.

EAS commands for the configured profile:

```powershell
cd mobile
npx eas build --platform android --profile production
npx eas submit --platform android --profile production
```

The current `submit.production.android` points at the **internal** track and
marks a release as a **draft**. That is suitable for preparing an internal
upload; it is not a production rollout. Decide explicitly whether to upload
from Play Console to a closed testing track or change the EAS submit track.
Do not change the track until the owner is ready to submit the artifact.

## 2. Store listing and assets

- [ ] Review the draft copy in [`android-version/STORE-LISTING.md`](../android-version/STORE-LISTING.md).
      Remove unsupported performance, school-approval, safety, or AI claims.
- [ ] Prepare the Play listing title (30 characters maximum), short
      description (80 maximum), full description (4,000 maximum), category,
      tags, default language, and any translations. Keep screenshots and copy
      suitable for a general audience and consistent with the declared
      audience.
- [ ] Enter an owner-monitored support email, support website, and the active
      privacy URL: <https://intelliplan.tech/legal#privacy>.
- [ ] Add an app icon for the listing at 512×512 PNG within Play's size and
      format requirements. The repository's source icons are 1024×1024; export
      and inspect a compliant listing asset rather than assuming the launcher
      icon is the Play listing asset.
- [ ] Add the feature graphic at 1024×500, JPEG or opaque 24-bit PNG. The
      checked-in `mobile/assets/play-feature-graphic.png` is 1024×500 RGB and
      about 38 KB; preview the exact upload in Play Console.
- [ ] Capture at least two genuine phone screenshots from the release build.
      Use demo/test data, never another student's personal information. Add
      tablet screenshots only if the owner intends to list/support tablet
      layouts; verify those layouts on devices first.
- [ ] Confirm all store graphics accurately represent current product
      screens and have rights for every included logo, font, and image.

Google's current asset rules and supported screenshot sizes are documented in
[Play preview asset requirements](https://support.google.com/googleplay/android-developer/answer/9866151)
and its [store listing setup guide](https://support.google.com/googleplay/android-developer/answer/9859152).

## 3. App content, privacy, and policy declarations

- [ ] Complete the Play Console App content declarations, including app
      access/reviewer instructions, ads, financial features if applicable,
      sensitive permissions, content rating, and target audience. Enter only
      answers approved by the owner after reviewing the actual release.
- [ ] Choose target age groups only after the owner and qualified counsel
      resolve the intended launch countries and age range. The engineering
      review records open questions about under-13 parental verification,
      school authorization, child-facing AI providers, and regional
      requirements. Selecting any child age group can trigger Google Play
      Families policy requirements. See
      [`docs/compliance/2026-10-02-policy-audit.md`](../docs/compliance/2026-10-02-policy-audit.md)
      and the official [target audience guidance](https://support.google.com/googleplay/android-developer/answer/9867159).
- [ ] Complete the Data safety form from a fresh review of the exact release
      build, production API flows, enabled providers/SDKs, and current
      retention/deletion operations. Review the data inventory and unresolved
      decisions in the policy audit above; inspect production routes and
      provider settings before answering each collection, sharing, purpose,
      optionality, encryption, and deletion question.
- [ ] Treat [`android-version/DATA-SAFETY.md`](../android-version/DATA-SAFETY.md)
      as an investigation worksheet only, not as approved Play Console
      answers. It contains statements that must be reconciled with the merged
      manifest and current production behavior.
- [ ] Enter the account deletion URL
      <https://intelliplan.tech/delete-account> and verify it works without
      installing the app or signing in. Verify the in-app Settings deletion
      path on the release build too.
- [ ] Review the exact privacy policy shown to users and ensure it matches
      the shipped features, providers, school integrations, and operational
      retention. A source-code review does not establish legal compliance.
- [ ] Resolve the owner/operator identity, monitored contact details, launch
      countries, age eligibility, school/provider permissions, child privacy
      safeguards, and legal review before a broad public launch. The policy
      audit explicitly is not legal clearance.
- [ ] If monetization is enabled later, complete the separate adult
      contracting, pricing/renewal, cancellation/refund, taxes, receipt, and
      Google Play billing-policy review before enabling it. The current app
      release configuration does not itself establish that billing is ready.

Use Google's current [Data safety instructions](https://support.google.com/googleplay/android-developer/answer/10787469),
[review preparation](https://support.google.com/googleplay/android-developer/answer/9859455),
and [content rating requirements](https://support.google.com/googleplay/android-developer/answer/9859655).

## 4. Test tracks and Android device sign-off

- [ ] Upload the production `.aab` to an internal test track first. Review
      Play's pre-launch report and resolve crashes, ANRs, policy warnings, and
      device-specific failures.
- [ ] Run a closed test with invited testers and a documented feedback
      channel. Check the Play Console account type and creation date before
      assuming a production-access requirement. Google currently requires
      personal developer accounts created after November 13, 2023 to keep at
      least 12 testers opted in continuously for 14 days before applying for
      production access. See [Google's current testing rules](https://support.google.com/googleplay/android-developer/answer/14151465).
- [ ] Install the exact Play-distributed build on real Android devices,
      including a current supported Android release and a smaller screen.
      Verify signup, login/logout, password recovery, account deletion,
      school/calendar connection, task creation, plan generation, tutoring,
      grades, camera/library photo attachment, permission denial, offline
      recovery, deep links, back navigation, keyboard/safe areas, light/dark
      theme, and TalkBack labels/focus.
- [ ] Test notification permission timing, local reminders, and remote push
      separately. Test remote push with production-like FCM/EAS credentials,
      app foreground/background/terminated states, sign-out, and revoked
      permission. Mark remote reminders unavailable in release copy if the
      service cannot be verified.
- [ ] Provide Play reviewers a stable, least-privilege test account and clear
      access instructions for features behind sign-in. Use test data only;
      document any feature that depends on a school connection or unavailable
      external account.
- [ ] Record build number, device/OS matrix, test account used, results,
      outstanding defects, and tester feedback. Obtain owner sign-off for the
      exact artifact and listing.

## 5. Production rollout and rollback

- [ ] Confirm all Play Console tasks and reviews are complete, the production
      bundle has the expected package/version/target API, and policy answers
      match the artifact and live service.
- [ ] Choose the launch countries and rollout strategy with the owner. Prefer
      a staged rollout if available for the account; monitor crashes, ANRs,
      login, account deletion, API health, tutoring, and reminder delivery.
- [ ] Keep a release record with the Play release name, version code, AAB
      checksum, source commit, EAS build ID, reviewer instructions, and
      approval/sign-off.
- [ ] Define who is on point for support and production response during the
      rollout. If a release causes serious issues, halt or pause further
      rollout in Play Console, notify affected users when required, and
      prepare a fixed higher-version-code release. Android app updates cannot
      be rolled back by installing a lower version code over the current one.

## Go / no-go sign-off

Public production release is ready only after each relevant item is signed
off by the owner:

| Area | Sign-off |
| --- | --- |
| Legal operator, audience, countries, privacy and school/provider decisions | Owner + qualified counsel where needed |
| Play account, package, signing, listing, declarations and reviewer access | Play Console owner |
| Android release build and physical-device test results | Engineering / QA owner |
| Support contact, staged rollout, monitoring and rollback contact | Product / operations owner |

## Related project notes

- [`android-version/PLAY-STORE-CHECKLIST.md`](../android-version/PLAY-STORE-CHECKLIST.md)
  and [`android-version/README.md`](../android-version/README.md) contain the
  original Android handoff notes; this file is the current release checklist.
- [`AppStore-Launch/README.md`](../AppStore-Launch/README.md) is the parallel
  iOS checklist. Reuse its physical-device QA and legal-review gates where
  relevant; complete Android-specific Play Console tasks above separately.
- [`mobile/app.json`](../mobile/app.json), [`mobile/eas.json`](../mobile/eas.json),
  and [`mobile/README.md`](../mobile/README.md) are the configuration/build
  sources of truth.
