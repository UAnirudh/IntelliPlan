# Data Safety preparation worksheet

Google holds the developer responsible for the accuracy of these
declarations, so treat this as a starting point to verify rather than an
answer key. What follows was read off the code in this repo; what production
actually has enabled is the thing that counts.

## Permissions the Android build requests

Declared explicitly in `mobile/app.json` so the manifest is intentional
rather than whatever the plugins happened to add:

| Permission | Why | Where |
| --- | --- | --- |
| `POST_NOTIFICATIONS` | Deadline and study-session reminders the student opted into. | `expo-notifications`, `mobile/lib/push.ts` |
| `CAMERA` | Snap & Solve: photograph a problem for the AI tutor. | `mobile/app/(tabs)/plani.tsx` |
| `READ_MEDIA_IMAGES` | The same feature, picking an existing photo instead. | as above |

Blocked, so they never reach the manifest: `RECORD_AUDIO`,
`READ_MEDIA_VIDEO`, `READ_EXTERNAL_STORAGE`, `WRITE_EXTERNAL_STORAGE`. The
image picker is configured for `mediaTypes: ["images"]`, so none of them are
used, and an unused permission on a listing is a review risk and a Data
Safety mismatch at once.

## Data the app handles

- **Account identifiers** — email, display name, password hash, auth token,
  birth year (collected for the under-13 gate), optional phone number.
- **Education data** — courses, assignments, grades, schedules, goals, focus
  sessions, task history, mastery and streaks.
- **User content** — notes, tutor conversations, uploads, and photos sent to
  Snap & Solve.
- **Connected-account data** — whatever the student authorises from Canvas,
  StudentVue, Schoology, Google Classroom, Google Calendar, Blackboard,
  Moodle, and Notion, plus the access tokens for each.
- **Device/app data** — push token, app version, request metadata, cached
  tasks.

Two things worth declaring precisely rather than loosely:

- **Focus check-in is not biometric data and is not a camera upload.** The
  pipeline runs in the browser/app; what crosses the network is a per-bucket
  count of how many samples read as present, away, or absent, plus a mean
  confidence. No frames, no embeddings, no face geometry. See the privacy
  contract at the top of `intelliplan/models/active_session.py`.
- **Snap & Solve photos do leave the device**, base64-encoded, to whichever
  AI provider is configured. That is a genuine "user content, shared with
  third parties" answer, not an optional one.

## Verify in Play Console

- [ ] Each category: collected? shared? with Railway, Google, Canvas, the AI
      providers, the email provider, the push provider?
- [ ] Encryption in transit (yes — HTTPS throughout) and at rest.
- [ ] Required versus optional collection, per category.
- [ ] Target-audience and Families treatment. See the note in
      `PLAY-STORE-CHECKLIST.md`: this app has under-13 handling built in, so
      the answer here has consequences.
- [ ] Account deletion: in-app at Settings → Delete account, and publicly at
      `https://intelliplan.tech/delete-account`.

## What deletion actually removes

Worth reading before filling in the deletion section, because the form asks
whether *all* data is deleted and the honest answer has exceptions:

- Removed: profile, credentials, every integration and its tokens, tasks,
  schedules, day archives, grades, study and focus sessions, streaks, points,
  mastery, tutor conversations, lessons, notes, notifications, push
  registrations, API keys, study-group membership and authored group tasks,
  and the lifecycle-email ledger.
- Kept, deliberately: study groups the user created (they belong to the other
  members too — the group survives ownerless), the email suppression list
  entry if they ever unsubscribed (keyed by address precisely so that
  unsubscribing outlives the account), aggregated de-identified statistics,
  and encrypted backups which age out within 90 days.

That list is mirrored in user-facing language at `/delete-account`; keep the
two in step.
