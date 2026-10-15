# Local network camera gateway

The study page accepts a video-only HTTPS WHEP endpoint. For an IP camera
that exposes RTSP, run MediaMTX **v1.21.2** on a computer on the camera's
network. An ONVIF camera can use its RTSP media-profile URI here; IntelliPlan
does not discover ONVIF devices or manage their profiles. MJPEG-only feeds,
HLS-only feeds and incompatible codecs need a separate conversion step and
are not automatically supported by this configuration.

1. Download the matching release from the [official MediaMTX release](https://github.com/bluenviron/mediamtx/releases/tag/v1.21.2).
   Check its archive against that release's SHA256 checksums. Extract it in
   a private local directory outside your repository. Keep the license with it.
2. Copy `mediamtx.example.yml` to that directory as `mediamtx.yml`. Replace
   all `REPLACE` values. Use the camera manufacturer's exact RTSP stream URI,
   a dedicated camera read account and a unique gateway password. Store this
   configuration privately; an RTSP URI can contain camera credentials.
   The example exposes only the `study` path for reading and disables recording,
   publishing, management APIs and other listeners.
3. Install a certificate/key trusted by the receiving browser for your gateway
   hostname. Save them as `server.crt` and `server.key`. A private CA is usable
   only when the receiving device trusts it. Browser certificate bypasses are
   not part of the product. Set `webrtcAdditionalHosts` to the gateway's LAN IP.
4. Run `mediamtx mediamtx.yml` (Windows: `mediamtx.exe mediamtx.yml`). Permit
   receiving devices to reach TCP8889 and UDP8189 on that LAN. Restrict these
   listeners with the host firewall to your intended private network. Do not
   expose them to the public internet or add router port forwarding. No TURN
   or external ICE service is configured; routed/VPN/firewall networks may fail.
5. On a deployment with enforced CSP, the operator must add the exact gateway
   origin to `STUDY_CAMERA_GATEWAY_ORIGINS`, for example
   `https://camera.home.example:8889`. The comma-separated list is bounded to
   sixteen HTTPS origins and expands connections only on `/active`. It does
   not permit wildcard origins or loosen the login/account pages. In report-only
   deployments unlisted origins generate CSP reports; configure the list before
   enabling enforcement. This is configuration, not a new gateway service.
6. In **Study → Camera source → Network camera or RTSP gateway**, enter
   `https://camera.home.example:8889/study/whep`, username `study-viewer` and
   its gateway password. The gateway must allow the IntelliPlan origin via CORS
   and expose the WHEP `Location` response header. MediaMTX provides this;
   any intervening proxy must preserve it, allow Authorization/Content-Type
   and permit POST/DELETE/OPTIONS without redirects. Enable the optional
   check-in explicitly after filling the form.

Credentials are sent directly to the gateway using Basic or Bearer auth over
HTTPS. They are not persisted or sent to IntelliPlan. Gateway URLs and credentials
stay in this page's memory until it closes; clear the fields on a shared device.
The gateway receives the browser's network metadata. IntelliPlan receives only
the existing opted-in timer/check-in summary, not video or SDP for this source.
Stopping, changing the source or losing permission closes reception and sends a
best-effort WHEP DELETE. A failed network can delay gateway resource cleanup;
use the gateway's own timeout/observability. Stopping IntelliPlan does not turn
off the physical camera or erase any recordings independently made by it.

Troubleshooting: check trusted HTTPS, exact CORS origin and exposed Location,
the deployment's CSP allowlist, the browser's local-network permission and LAN
firewall, then the camera codec. H264 baseline without B-frames is a useful
compatible test source. IntelliPlan requests video only and does not request
microphone permission. Camera movement or presence is not proof of studying.

## Reproducible synthetic verification

Install Python dependencies plus Playwright/Chromium and FFmpeg. Set
`MEDIA_MTX_BINARY` to the extracted, checksum-verified v1.21.2 executable, then
run `python scripts/verify_network_camera_browser.py` from the repository root.
The verifier creates disposable SQLite data, a one-day test certificate and
loopback-only listeners. It publishes FFmpeg test frames over real RTSP and
checks decoded browser video through real HTTPS WHEP, explicit shutdown and
permission revocation. Only this synthetic verifier ignores its test certificate.
It stops its processes on exit. Logs, test keys and screenshots go under ignored
`.codex/verification/network-camera-browser`; CI uploads only screenshots/results.
This verifies a synthetic codec/transport path, not a physical IP/ONVIF camera.

References: [MediaMTX configuration](https://mediamtx.org/docs/references/configuration-file),
[WebRTC support](https://mediamtx.org/docs/features/webrtc-specific-features),
[authentication](https://mediamtx.org/docs/features/authentication).
