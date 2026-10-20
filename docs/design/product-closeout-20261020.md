# Product closeout - October 20, 2026

This continuation excludes database migration and does not deploy the site.

## Product fixes

- Light-theme user messages now use the theme's readable foreground instead of
  inheriting white text. Replies keep a consistent paragraph and heading rhythm.
- Appearance adds persisted standard, large and extra-large reading sizes.
  Navigation retains its size; the appearance dialog scrolls on shorter screens.
- Desktop suggested questions use available horizontal space; shorter laptop
  windows reduce the welcome illustration's height to leave room for controls.
- Foundations explains the actual prerequisite rule, shows open/review counts
  and review dates, and offers existing offline activities without recording
  unobserved work as scored evidence. Failed activities/path loads have retry
  actions; timed-out requests restore interaction and preserve answer drafts.
- Admin permission lists and the three adult/student relationship lists batch
  related records. Tests with forty records require exactly three SELECTs per
  listing, preserving owner, consent and active-session revocation boundaries.
- Adult work overviews apply the student's source policy to stored imported
  open/completed tasks; personal tasks remain available. Unknown imported sources
  fail closed.

## Verification

- Foundations: 27 SQL regressions, 18 browser flows, six responsive layouts and
  two dark previews passed, with zero page errors or horizontal overflow.
  Grade changes were checked against actual API responses; retries preserved
  drafts and counted each answer once. Independent frontend review passed.
- Adult/teacher/family: 17 focused tests passed; latest adult-access suite passed
  11 tests including query counts, isolation, revocation and source policy.
  Independent Python review found no high or medium defects.
- Audio/microphone: 22 runtime tests passed. No-provider server responses retain
  browser speech fallback; explicit 401/403 policy denials prevent fallback.
  Earlier real HTMLAudioElement decoding/playback evidence remains applicable.
- Shared UI: seven fresh browser checks passed, including user-message contrast
  of 14.81:1, saved reading size, extra-large phone text, focus restoration,
  sanitized Markdown/JSON, dark theme and the phone composer. Three hundred
  slider inputs retained every SVG node with zero child-list replacements.
  Results: output/playwright/workspace-v2/interactions-report.json.
- Scheduler benchmark recorded under .codex/verification. This host was heavily
  contended, so its absolute durations do not establish a speedup.

Live provider voice quality, physical speakers/microphone and live connected-LMS
network latency are not established by synthetic browser fixtures. Existing
unsupported/beta tools retain truthful restrictions. No production release is
claimed by these local changes.
