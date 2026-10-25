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
- Account deletion: in-app Settings flow and `https://intelliplan.tech/delete-account`

The EAS project is not linked yet (`extra.eas.projectId` is empty), and EAS CLI
is not authenticated in this checkout. Apple team and App Store Connect app
details must be supplied by the app owner before a signed build can be made.

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
      included SDK/provider disclosures. The old privacy table in this folder
      was not verified against the shipped native binary; do not copy it as-is.
- [ ] Add genuine screenshots from the release build on supported iPhone and
      iPad sizes. Do not use mockups or another student's real data.
- [ ] Provide a working review account and notes for sign-in, school imports,
      age gates, AI tutor, photo use, and any feature needing external setup.
- [ ] Install the TestFlight build on physical devices; test signup, sign-in,
      sign-out, account deletion, school connection, study flows, camera/photo
      permissions, keyboard/safe areas, VoiceOver, offline recovery, and push
      behavior.
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
