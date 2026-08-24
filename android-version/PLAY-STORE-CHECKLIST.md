# Play Store submission checklist

## Play Console

- [ ] Create/select the Google Play Developer account.
- [ ] Create an app with package name `tech.intelliplan.app`.
- [ ] Complete developer/app-signing agreements and contact details.
- [ ] Complete any required closed-test period for the developer account.

## Build and signing

- [ ] Run `npx eas login` and `npx eas project:init` from `mobile/` if needed.
- [ ] Configure Android App Signing with `npx eas credentials --platform android`.
- [ ] Back up the keystore securely; never commit it.
- [ ] Upload the Google Play service-account JSON to EAS credentials.
- [ ] Build: `npx eas build --platform android --profile production`.
- [ ] Confirm the artifact is an `.aab` and its version code is new.
- [ ] Install and test the release build on a physical Android device.

## Store listing

- [ ] App name, short/full descriptions, support email, and website.
- [ ] Icon, feature graphic, and current phone screenshots.
- [ ] Tablet screenshots if tablet support remains enabled.
- [ ] Privacy policy: `https://intelliplan.tech/legal#privacy`.
- [ ] Account deletion instructions: in-app Settings → Delete account and the
      public privacy/deletion information.
- [ ] Reviewer test-account/access instructions if needed.

## App content and policy

- [ ] Data Safety form using `DATA-SAFETY.md` as a verified starting point.
- [ ] Content rating, target audience, Families, ads, and financial-features forms.
- [ ] Account deletion/data deletion form.
- [ ] Permission explanations for camera, photos, and notifications.

## Release testing

- [ ] Sign up, sign in, sign out, and delete an account.
- [ ] Verify Railway production API/login from the release build.
- [ ] Connect Canvas and Google Calendar through production browser flows.
- [ ] Create a task, generate a plan, complete a task, and use focus mode.
- [ ] Test offline queue replay, push notifications, deep links, back navigation,
      keyboard behavior, and dark mode.
- [ ] Upload to Internal testing, resolve pre-launch report issues, then promote.

Official references:

- https://support.google.com/googleplay/android-developer/answer/9859152
- https://support.google.com/googleplay/android-developer/answer/10787469
- https://support.google.com/googleplay/android-developer/answer/13327111
- https://docs.expo.dev/submit/android/
