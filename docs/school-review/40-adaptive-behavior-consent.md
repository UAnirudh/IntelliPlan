# Adaptive behavior fitting follows personalization consent

Adaptive recommendations previously fitted a local behavior model even when
AI personalization was off; only their AI narration respected that setting.
`NextActionService` now defaults to no personalization and requires an explicit
owner-specific permission callback before loading behavioral session, feedback,
daily-minute and reschedule histories. It rechecks permission before fitting
the model and discards loaded history if consent changed or failed.

The production callback reads fresh owner consent and source permissions.
Direct adaptive history helpers also deny opted-out reads. Ordinary plan/task
recommendations, current active-session state, mastery records and operational
action/version audits remain available. This is a bounded change to adaptive
recommendations, not a global opt-out from every local scheduler profile or a
deletion/retention guarantee. Other legacy consumers still need review.

Availability calculations no longer invoke the legacy history/profile builder
whose behavioral output they discarded. They read the owner's stated
`UserIdentity` availability and weekly commitments directly, with permission
checks around the read and on retained window callbacks. No identity row is
created by this read.

Settings now explains permitted AI-provider sharing and the adaptive history
use. The contradictory statement that data never leaves the account is removed.
The existing off-by-default toggle is reused; no new control is introduced.

Tests cover opted-out and unavailable permission, consent changing during a
read, direct history query traps, real HTTP recommendations with the actual
availability path, and preserved plan/current-session behavior. Providers and
disposable storage are fixtures. Independent Python and spec review found an
indirect availability read; the correction was re-reviewed successfully.
The final focused suite passes342 tests; scoped Ruff and whitespace checks pass.
Live authenticated users, all behavioral consumers, retention and broader
original acceptance criteria remain open.

PR90 is merged as `edaf83b88861b3101e4b07585b3e1cc911c3203b`, with Railway
production deployment `7faa9e4a-b45d-478d-a315-8273b1d53fff` at SUCCESS. The
live polling helper confirms that exact public health revision. Post-release
hosted checks pass4519 unit tests with16 skipped, then the exact-revision wait,
then130 public browser tests with1 skipped; all security jobs pass.
