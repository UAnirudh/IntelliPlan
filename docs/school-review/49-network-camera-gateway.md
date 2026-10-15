# Network study cameras and RTSP gateways

The shared desktop/phone Camera source menu now accepts an HTTPS WHEP gateway,
with optional Basic username/password or a Bearer access token. Selecting the
source leaves capture off; entering/changing its connection details stops an
old connection and requires explicit re-enabling. Receiving video uses the
existing on-device preview/check-in pipeline and does not require a local
webcam. Audio is not negotiated. This does not establish study accuracy.

The browser sends SDP and gateway credentials directly to that gateway, never
through Railway. It omits cookies/referrers, rejects redirects and embedded URL
credentials, and accepts a session resource only on the gateway's exact origin.
The gateway must provide trusted HTTPS, CORS and an exposed WHEP Location header.
Browser local-network permissions and firewall rules still apply. No STUN/TURN
service is configured. Responses and connection waits are bounded; video-only
SDP is required. Stop, source replacement, failed connections and fresh account/
school permission denial close the peer, stop received tracks and attempt
session deletion. Late responses cannot revive capture and their session
resources are deleted when available. Deletion is best effort on network failure.

Gateway details are held in the current page, not persisted. They are sent to
the entered gateway for authentication; the page explains this. The gateway and
camera have their own storage/privacy responsibilities. IntelliPlan does not
control a physical camera's power or independent recordings.

The operator-controlled `STUDY_CAMERA_GATEWAY_ORIGINS` configuration permits up
to sixteen explicit HTTPS origins in the study page's CSP. Wildcards, paths,
credentials and injected directives are rejected. Other pages retain their
existing connection policy. Strict CSP deployments require this list; the
default report-only mode reports unlisted origins. No deployment allowlist was
populated with an invented camera address.

[Local gateway instructions and configuration](../../deploy/study-camera/README.md)
provide a pinned MediaMTX v1.21.2 RTSP-to-WHEP setup, read-only gateway access,
trusted certificates, a private-network deployment and recording disabled.
An ONVIF camera can use its manufacturer's RTSP media-profile URI. Automatic
ONVIF discovery/profile control, arbitrary MJPEG/HLS conversion, incompatible
codecs, cross-network relays and physical-device testing are still incomplete.
No gateway was installed on Railway or attached to a production camera.

Verification so far: 13 Node regressions cover endpoint restrictions, auth
isolation, bounded responses, audio rejection, stale responses, revocation and
teardown. Two Python tests verify the scoped CSP and hostile configuration;
17 focused camera/navigation/CSP tests pass. The real browser verifier runs
FFmpeg synthetic H264 through real RTSP into the checksum-verified MediaMTX
release and receives decoded video over actual HTTPS WHEP, with real enforced
App CSP headers and disposable Flask/SQLite APIs. Desktop received 26 frames,
zero audio tracks, no page errors, no unexpected external request attempts,
and retained its ordinary timer after capture stopped. Optional font requests
are blocked and reported separately. Independent authenticated loopback gateway
inventory confirms one active session while connected and zero after stopping
or revoking permission. DELETE may return 404 when closing the peer already
removed its resource; a sent request alone is not the cleanup proof. The test
certificate bypass applies only to this synthetic loopback fixture.

The phone layout also decoded40 frames with the same stop/revocation, zero-audio, empty-gateway-inventory and no-overflow checks passing. The full local suite reported4653 passed,33 skipped and2 failures: a5-second Windows trusted-Python process timeout and newsletter Git/snapshot inputs differing after a Git timeout. All50 curated/newsletter cases pass on a focused rerun; hosted full checks remain the release gate. CI now
installs the pinned checksum-verified gateway and FFmpeg, runs both layouts and
uploads only screenshots/results, excluding fixture TLS keys/configuration.
Independent JavaScript/spec and Python/security reviews corrected the initial
CSP/disclosure gaps and found no remaining actionable issue in this scope.

Delivery: network-camera source commit4cd4a57 is pushed in PR100, pending hosted checks, merge and deployment. PR99
direct phone pairing is merged and live at
1630f1bba2bb1e6adf1499947781bd3d6d13c2e2. Its final local suite passed
4653 tests with33 skipped; hosted pre/post-merge Tests and Security passed.
Railway confirms web deployment57ac909a-f1a4-4b62-b750-6e14bed207ee SUCCESS,
one running replica, no active warnings and no pending operations; the exact
public revision/schema check passed. Filtered ERROR/Traceback/CRITICAL logs for this deployment since16:50:49UTC contain zero entries at inspection, a bounded observation rather than a general error-free claim. This establishes release health, not
physical camera compatibility. Fish playback, real tutor pedagogy, Stripe
transaction proof and the broader original acceptance ledger remain open.
