# Study camera inputs and session continuity

The optional camera check-in now has one source menu shared by desktop and
phone layouts. It lists browser-visible webcams, USB/capture inputs and
virtual cameras, with a back-camera preference on phones. Explicit device
selection requests that exact device; an unavailable selection never silently
opens another camera. The menu does not request capture permission by itself.
Device labels and identifiers remain in the page, without cross-session storage
or server telemetry. The implementation follows
[MediaDevices enumeration](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/enumerateDevices)
and [camera constraints](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).

Changing cameras stops the previous tracks and preserves session observations.
Late permission/capture responses are discarded after cancellation or a newer
selection. Terminal errors and disconnection stop the tracker, preview and
enforcer. Pause stops capture; resuming restarts only when check-ins remain
selected. Leaving the page turns check-ins off, including when restored from
the back/forward cache. Phone layouts now include the same optional self-view.
Their study action bar sits above the app navigation rather than underneath it.

Summary counts and their original session-relative offsets survive camera
restarts and pauses; pauses do not extend focus streaks. Reloaded sessions seed
the exact saved aggregate counts. Atomic database updates retain newer totals
when older packets or stale ORM instances arrive. Late camera opt-in can enable
summary recording after the ordinary timer has started. The historical
`focus_enabled` session field means summaries were enabled during that session;
the browser's toggle/status reports whether capture is currently running.

Finishing a session now banks its final elapsed interval before stopping the
timer and uses the shared teardown for preview, capture and enforcement. This
fixes a pre-existing loss of the last active segment on completion.

Fresh central focus policy gates controls, camera permission checks, initial
and late recording, heartbeat summaries and final summaries. Camera opening
and switching first request current permission. A denied response stops capture;
the ordinary study timer remains usable. A regression revokes permission during
progress persistence: the timer update survives, while new summary data is
discarded. Previously collected observations remain intact.

The visible opt-in disclosure says video stays on-device and aggregate counts
are saved to the account. Presence/movement are explicitly estimates, not proof
of studying. Motion can come from the background; it does not establish that a
person is present, attentive or learning. No frames, recordings or landmarks
are sent by this pipeline. Camera accuracy has not been independently validated.

Verification: 13 Node runtime regressions pass, plus 93 focused Python tests
including real Flask/SQLite late opt-in, delayed summaries and policy denial/
revocation. The rendered desktop and actual phone templates pass Chromium
workflows using fake capture and real disposable application persistence, with
no page errors or horizontal overflow. Saved observations increase from 3
before pause to 5 after resume; completion preserves 13/12 active seconds in
the respective fixtures. The browser verifier is reproducible in
`scripts/verify_study_camera_browser.py` and runs in CI, alongside the Node
tests; CI saves UI screenshots as a browser-proof artifact. Independent
JavaScript, Python and spec reviews rechecked the corrected findings and found
no remaining actionable defects in this scope. Hosted results follow separately.

Remaining: physical USB/capture/phone/device validation; direct phone companion
pairing; direct HTTPS network camera streams and bridges for RTSP/ONVIF; stronger
on-device signal models and independent measurement-quality evaluation. Phones
or network cameras exposed as system webcams can use this source picker, but
that does not implement the missing direct connection paths. The user's broad
all-camera requirement is therefore still partial. The question-led visual/audio
tutor, paid launch readiness and the rest of the original brief remain open.
