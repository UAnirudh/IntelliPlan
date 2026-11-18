# Workspace UI and study updates

The app workspace uses quieter surfaces, smaller corners, ordinary labels and
restrained controls. The reference is Notion's sidebar/editor separation and
collapsible secondary navigation:
https://www.notion.com/help/navigate-with-the-sidebar
No third-party UI script or tracker was added.

## Changed behavior

- Command Center appearance can change width, response presentation, spacing,
  text size, plan layout and accent. Preferences stay in per-account browser
  storage; no conversation or school data is stored in these preferences.
- Sidebar collapse dismisses hover expansion immediately and waits for the
  pointer to leave before peeking again. Expand remains visible in the rail.
  Blocked browser storage no longer breaks toggling.
- Report a bug is a sidebar action rather than an overlapping floating button.
- Scheduler secondary inputs and plan actions are in native disclosures.
- Active Study lets students select school or manual assignments before
  starting. Selection is locked during a session. Existing timer-only study
  and saved-plan suggestions remain available if assignment loading fails.
- Background autopilot fetches fresh, explicitly owner-scoped assignments
  before replanning, including new work and submitted work status. The existing
  scheduler runs every four hours by default (`AUTOPILOT_INTERVAL_HOURS`).
  Opt-out is checked before fetching. Age holds and scheduling policy remain
  enforced. It does not borrow another signed-in caller's saved descriptions.
- The visible day timeline refreshes every five minutes and on plan changes,
  returning to the tab, and the next local day. Drag/confirmation flows are
  left undisturbed.

## Owner preview and privacy boundaries

The verified configured owner `uanirudh0811@gmail.com`, in Founder mode, can
open `/teacher/demo` with fictional student data despite the account's age
hold. This exception is limited to that GET page. Birth year, role, real
student links and age/consent protections are unchanged. It does not grant
blanket access to restricted features or anyone else's student records.

Active Study's camera processing uses browser FaceDetector or local motion
comparison. Frames are not sent to an external AI model. Stopping releases
capture tracks and clears the canvas and prior motion buffer. Summary counts
are still persisted to the account. Paired devices exchange temporary encrypted
connection details; a user-selected network camera gateway is a separate
operator whose retention cannot be guaranteed by IntelliPlan code.

These changes do not certify COPPA/FERPA compliance or verify production
vendor agreements, settings, retention or school authorization. Production
deployment and the running background-job configuration need separate
verification. The original landing-page edits were preserved.

## Verification

190 targeted Python checks and 42 JavaScript checks passed. Syntax and patch
whitespace checks passed. Local browser verification used a disposable database
and fictional account: sidebar expand/collapse, appearance persistence and
Escape, scheduler disclosures, sidebar bug-report dialog, and selecting and
starting a 45-minute History assignment. No real student records were used.
Production deployment, production account changes and vendor retention settings
were not performed or verified in this pass.
