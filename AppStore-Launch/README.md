# IntelliPlan iOS App Store release

IntelliPlan is a native Expo / React Native app in `mobile/`. Do not use the
old PWABuilder website-wrapper instructions for this project.

## Current release configuration

- App: `IntelliPlan`
- Bundle ID: `tech.intelliplan.app`
- Version: `1.0.0` (advance for the first public release as appropriate)
- Build: EAS production profile, `mobile/eas.json`
- API: production URL `https://intelliplan.tech`, set by the production build
  profile
- Privacy policy: `https://intelliplan.tech/legal#privacy`
- Support: `https://intelliplan.tech/contact`
- Account deletion: in-app Settings flow and `https://intelliplan.tech/delete-account`

The EAS project is not linked yet (`extra.eas.projectId` is empty). The app
owner must link the project and supply Apple signing/submission access before
the release build and upload can happen. An App Store Connect app ID is needed
for a non-interactive EAS Submit profile; an interactive submit can prompt for
the app record. This repo intentionally does not contain account identifiers
or credentials.

## Build and TestFlight

EAS Build can produce an iOS archive from Windows; local Xcode is not required
for the cloud build. The Apple Developer account and EAS project are required.

```powershell
cd mobile
npx eas login
npx eas init
npx eas build --profile production --platform ios
npx eas submit --profile production --platform ios
```

The first build may prompt for Apple signing credentials. Review the bundle ID
and team carefully and keep signing credentials in EAS or the Apple account;
never commit them. `eas submit` uploads to App Store Connect / TestFlight. It
does not publish the app to the public App Store.

## App Store Connect checklist

- [ ] Create the App Store Connect app record using bundle ID `tech.intelliplan.app`.
- [ ] Confirm the publishing account owner and Apple Developer team.
- [ ] Add app name, subtitle, description, support URL, privacy URL, category,
      age rating, pricing, and release notes.
- [ ] Complete App Privacy from the current production data flows and all
      included SDK/provider disclosures. The privacy manifest in `app.json`
      is a separate binary declaration and does not fill out App Privacy for
      you. The old privacy table in this folder was not verified against the
      shipped native binary; do not copy it as-is.
- [ ] Add at least one genuine release-build screenshot at Apple's required
      iPhone Dynamic Island medium display size and, because this app supports
      iPad, at the iPad 13-inch display size. Do not use mockups or another
      student's real data. Check Apple's current [screenshot specifications]
      for accepted pixel sizes before export.
- [ ] Provide a working review account and notes for sign-in, school imports,
      age gates, AI tutor, photo use, and any feature needing external setup.
- [ ] Confirm the production archive was built with Xcode 26 or later and an
      iOS 26 SDK. Apple has required this for App Store Connect uploads since
      April 28, 2026; EAS selects the build image from the Expo SDK unless an
      image is explicitly configured, so retain/check the EAS build details.
- [ ] Verify the archive contains valid privacy manifests for the app and any
      SDKs that require them, and that every required-reason API declaration
      matches actual use. A JSON config check cannot validate the built IPA.
- [ ] Install the TestFlight build on physical devices; test signup, sign-in,
      sign-out, account deletion, school connection, study flows, camera/photo
      permissions, keyboard/safe areas, VoiceOver, offline recovery, and push
      behavior.
- [ ] Configure the Apple push key through EAS and verify remote notifications
      on the signed TestFlight build. The Expo notifications plugin adds the
      APNs entitlement during config generation; a local config check cannot
      verify the provisioning profile or APNs credential.
- [ ] Resolve any App Store Connect processing, privacy, export compliance, or
      reviewer questions before submitting for App Review.

## Decisions that block a responsible public release

The engineering privacy review at
`docs/compliance/2026-10-02-policy-audit.md` records unresolved launch decisions
for the legal operator and contact, intended countries and eligible ages,
under-13 parental verification, school authorization, and child-facing AI
provider terms. The review explicitly says it is not legal clearance. Resolve
those decisions with the responsible operator and qualified counsel before
representing the app as compliant or making it broadly available to children
or schools.

Apple requires accurate privacy disclosures, a privacy policy link, and
reviewer access to account-based features. See the current [App Review
Guidelines](https://developer.apple.com/app-store/review/guidelines/) and
[App Store Connect submission overview](https://developer.apple.com/app-store/submitting/).

### Static iOS preflight

From `mobile/`, run the iOS configuration checks before requesting a build:

```powershell
npm run release:preflight:ios
npm run test:release-preflight
```

The preflight checks the configured iOS identity/version, physical-device
production profile, HTTPS API, purpose strings, and privacy-manifest shape. It
also reports missing EAS linkage/authentication as blockers and missing Apple
IDs as owner actions. It never prints credentials, contacts Apple, signs or
builds the app, or reports that an IPA is ready. A passing static preflight is
not an App Store approval or a replacement for the checklist above.

[Screenshot specifications]: https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications
