"""Decode synthetic RTSP video through a real, separately supplied MediaMTX gateway.

Requires MEDIA_MTX_BINARY (v1.21.2) and ffmpeg on PATH. Uses loopback-only
listeners, disposable storage and test TLS; never contacts a real camera.
"""
import datetime as dt
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import time
from threading import Thread
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(DATABASE_URL='sqlite:///:memory:', SECRET_KEY='network-camera-fixture',
                  APP_BASE_URL='https://127.0.0.1:18443', NOTIFICATIONS_INPROCESS_CRON='0',
                  FOLLOWTHROUGH_INPROCESS_CRON='0', CSP_ENFORCE='1',
                  STUDY_CAMERA_GATEWAY_ORIGINS='https://127.0.0.1:18889')
import App  # noqa: E402 -- set disposable storage before importing app.

VIEW = os.getenv('NETWORK_CAMERA_VIEW', 'desktop')
OUT = Path(os.getenv('NETWORK_CAMERA_OUTPUT', str(ROOT / '.codex/verification/network-camera-browser' / VIEW)))
OUT.mkdir(parents=True, exist_ok=True)
BINARY = os.environ['MEDIA_MTX_BINARY']
FFMPEG = shutil.which('ffmpeg')
if not FFMPEG:
    raise RuntimeError('ffmpeg must be installed before this verification')
APP_ORIGIN = 'https://127.0.0.1:18443'
GATEWAY = 'https://127.0.0.1:18889'
App.limiter.enabled = False
with App.app.app_context():
    App.db.create_all()
    user = App.User(email='network-camera@example.test', password_hash='x', birth_year=2000)
    App.db.session.add(user)
    App.db.session.commit()
    uid = user.id
    App._get_or_create_identity(uid).completed = True
    App.db.session.commit()
api = App.app.test_client()
with api.session_transaction() as session:
    session['_user_id'] = str(uid)
    session['_fresh'] = True


def certificate():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost test fixture')])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
            .sign(key, hashes.SHA256()))
    (OUT / 'server.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                      serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()))
    (OUT / 'server.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def wait_port(port, process):
    for _ in range(80):
        if process.poll() is not None:
            raise RuntimeError('Synthetic video fixture exited; inspect local fixture log')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.2):
                return
        except OSError:
            time.sleep(.1)
    raise RuntimeError('Synthetic video fixture did not start')


certificate()
document = api.get('/active?view=' + VIEW)


class DocumentHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(document.status_code)
        for name, value in document.headers.items():
            if name.lower() not in ('set-cookie', 'content-length', 'connection'):
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(document.get_data())

    def log_message(self, *args):
        pass


server = HTTPServer(('127.0.0.1', 18443), DocumentHandler)
# Some desktop Python distributions inject a client-only truststore wrapper.
# Use its saved stdlib class for this local TLS server, without changing
# global/client certificate verification.
context_class = getattr(sys.modules.get(ssl.SSLContext.__module__), '_original_SSLContext', ssl.SSLContext)
tls = context_class(ssl.PROTOCOL_TLS_SERVER)
tls.load_cert_chain(str(OUT / 'server.crt'), str(OUT / 'server.key'))
server.socket = tls.wrap_socket(server.socket, server_side=True)
Thread(target=server.serve_forever, daemon=True).start()
config = f'''logLevel: warn
api: true
apiAddress: 127.0.0.1:19997
rtspAddress: 127.0.0.1:18554
rtspTransports: [tcp]
rtmp: false
hls: false
srt: false
moq: false
webrtcAddress: 127.0.0.1:18889
webrtcEncryption: true
webrtcServerKey: {json.dumps(str((OUT / 'server.key').resolve()))}
webrtcServerCert: {json.dumps(str((OUT / 'server.crt').resolve()))}
webrtcAllowOrigins: [{json.dumps(APP_ORIGIN)}]
webrtcLocalUDPAddress: 127.0.0.1:18189
webrtcIPsFromInterfaces: false
webrtcAdditionalHosts: [127.0.0.1]
webrtcICEServers2: []
authInternalUsers:
- user: fixture-admin
  pass: fixture-api
  ips: [127.0.0.1]
  permissions:
  - action: api
- user: publisher
  pass: fixture-publish
  ips: [127.0.0.1]
  permissions:
  - action: publish
    path: synthetic
- user: camera-reader
  pass: fixture-source
  ips: [127.0.0.1]
  permissions:
  - action: read
    path: synthetic
- user: viewer
  pass: fixture-read
  ips: [127.0.0.1]
  permissions:
  - action: read
    path: study
pathDefaults:
  record: false
paths:
  synthetic:
  study:
    source: rtsp://camera-reader:fixture-source@127.0.0.1:18554/synthetic
    sourceOnDemand: true
    rtspTransport: tcp
'''
config_path = OUT / 'mediamtx.yml'
config_path.write_text(config, encoding='utf-8')
processes = []
logs = []
errors = []
external = []
gateway_requests = []
gateway_responses = []
payloads = []
permission_allowed = True
blocked_optional_fonts = []
flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def gateway_sessions():
    req = Request('http://127.0.0.1:19997/v3/webrtcsessions/list',
                  headers={'Authorization': 'Basic ' + base64.b64encode(b'fixture-admin:fixture-api').decode()})
    with urlopen(req, timeout=3) as response:
        return len(json.load(response)['items'])


def wait_reclaimed(page):
    for _ in range(30):
        if gateway_sessions() == 0:
            return
        page.wait_for_timeout(100)
    raise AssertionError('gateway did not reclaim the WHEP session')

try:
    log = (OUT / 'gateway.log').open('w')
    logs.append(log)
    gateway = subprocess.Popen([BINARY, str(config_path.resolve())], stdout=log, stderr=log,
                               creationflags=flags)
    processes.append(gateway)
    wait_port(18554, gateway)
    log = (OUT / 'publisher.log').open('w')
    logs.append(log)
    publisher = subprocess.Popen([FFMPEG, '-nostdin', '-hide_banner', '-loglevel', 'warning', '-re',
                                  '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=10', '-an',
                                  '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-profile:v', 'baseline',
                                  '-tune', 'zerolatency', '-g', '10', '-f', 'rtsp', '-rtsp_transport', 'tcp',
                                  'rtsp://publisher:fixture-publish@127.0.0.1:18554/synthetic'],
                                 stdout=log, stderr=log, creationflags=flags)
    processes.append(publisher)
    time.sleep(2)
    if publisher.poll() is not None:
        raise RuntimeError('Synthetic RTSP publisher exited')
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv('STUDY_CAMERA_BROWSER_CHANNEL') or None)
        # Only this loopback fixture has a self-signed certificate. The product
        # code has no certificate bypass and requires a trusted gateway cert.
        context = browser.new_context(ignore_https_errors=True,
                                      viewport={'width': 390, 'height': 844} if VIEW == 'phone' else {'width': 1280, 'height': 900},
                                      is_mobile=VIEW == 'phone', has_touch=VIEW == 'phone')
        page = context.new_page()

        def route(handler):
            req = handler.request
            parsed = urlsplit(req.url)
            origin = f'{parsed.scheme}://{parsed.netloc}'
            if origin == GATEWAY:
                gateway_requests.append(req.method)
                assert not req.headers.get('cookie')
                assert not req.headers.get('referer')
                return handler.continue_()
            if origin != APP_ORIGIN:
                # Optional app fonts are blocked in this isolated fixture.
                if parsed.hostname not in ('fonts.googleapis.com', 'cdn.jsdelivr.net'):
                    external.append(origin)
                else:
                    blocked_optional_fonts.append(origin)
                return handler.abort()
            assert not req.headers.get('authorization'), 'gateway credentials leaked to app'
            if parsed.path == '/active':
                # A real TLS document connection establishes the browser's
                # loopback address space; route.fulfill would classify it unknown.
                return handler.continue_()
            if parsed.path.startswith('/static/'):
                path = (ROOT / parsed.path.lstrip('/')).resolve()
                if path.is_relative_to(ROOT / 'static') and path.is_file():
                    return handler.fulfill(status=200, body=path.read_bytes(),
                                           content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                return handler.abort()
            if parsed.path == '/api/active/focus-permission' and not permission_allowed:
                return handler.fulfill(status=200, json={'allowed': False})
            if parsed.path == '/active' or parsed.path.startswith('/api/active/') and parsed.path != '/api/active/next':
                response = api.open(parsed.path, method=req.method, data=req.post_data,
                                    content_type='application/json' if req.method == 'POST' else None)
                if req.post_data:
                    payloads.append(req.post_data)
                return handler.fulfill(status=response.status_code, body=response.get_data(),
                                       content_type=response.content_type,
                                       headers={k: v for k, v in response.headers.items()
                                                if k.lower().startswith('content-security-policy')})
            if parsed.path.startswith('/api/'):
                data = {'status': 'ok'}
                if parsed.path == '/api/active/next':
                    data = {'next': {'title': 'Synthetic RTSP camera study', 'course': 'Math',
                                     'planned_minutes': 25, 'block_id': 'test', 'task_id': 'test'}, 'has_plan': True}
                if parsed.path == '/api/focus/enforcement':
                    data = {'mode': 'off'}
                return handler.fulfill(status=200, json=data)
            return handler.abort()

        page.route('**/*', route)
        page.on('pageerror', lambda err: errors.append(str(err)))
        page.on('console', lambda msg: print('browser:', msg.text, flush=True) if msg.type == 'error' else None)
        page.on('requestfailed', lambda req: print('request failed:', urlsplit(req.url).path, req.failure, flush=True))
        page.on('response', lambda res: gateway_responses.append((res.request.method, res.status))
                if res.url.startswith(GATEWAY + '/') else None)
        page.goto(APP_ORIGIN + '/active', wait_until='networkidle')
        essential = page.get_by_role('button', name='Essential only', exact=True)
        if essential.is_visible():
            essential.click()
        page.locator('#ipaCameraMenu summary').click()
        page.locator('#ipaCameraSource').select_option('network')
        page.locator('#ipaNetworkUrl').fill(GATEWAY + '/study/whep')
        page.locator('#ipaNetworkUser').fill('viewer')
        page.locator('#ipaNetworkPassword').fill('fixture-read')
        page.locator('#ipaNetworkPassword').blur()
        page.locator('label[for="ipaFocusToggle"]').click()
        page.locator('#ipaStart').click()
        try:
            page.wait_for_function('window.IPFocus.activeCamera() !== null', timeout=30000)
        except Exception:
            print(json.dumps({'camera_status': page.locator('#ipaFocusNote').inner_text(),
                              'gateway_methods': gateway_requests, 'page_errors': errors}), flush=True)
            raise
        page.wait_for_function('window.IPFocus.activeCamera().video.videoWidth > 0')
        assert page.evaluate('window.IPFocus.activeCamera().stream.getAudioTracks().length') == 0
        page.wait_for_timeout(2500)
        frames = page.evaluate('''async () => {
          const stats = await window.IPFocus.activeCamera().remote.pc.getStats();
          return [...stats.values()].filter(r => r.type === 'inbound-rtp' && r.kind === 'video')
            .reduce((n, r) => n + (r.framesDecoded || 0), 0);
        }''')
        assert frames > 0
        assert gateway_sessions() == 1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT / ('network-' + VIEW + '.png')), full_page=True)
        page.locator('label[for="ipaFocusToggle"]').click()
        page.wait_for_function('window.IPFocus.activeCamera() === null')
        page.wait_for_timeout(500)
        assert 'DELETE' in gateway_requests
        # Closing the peer may reclaim it before DELETE arrives (404). Check
        # the real gateway's independent inventory, rather than infer cleanup
        # from a request being sent or insist that DELETE wins this race.
        wait_reclaimed(page)
        assert page.locator('#ipaPause').is_visible()
        page.locator('label[for="ipaFocusToggle"]').click()
        page.wait_for_function('window.IPFocus.activeCamera() !== null', timeout=30000)
        permission_allowed = False
        page.wait_for_function('window.IPFocus.activeCamera() === null', timeout=15000)
        assert page.locator('#ipaPause').is_visible()
        page.wait_for_function('!document.querySelector("#ipaFocusToggle").checked')
        wait_reclaimed(page)
        assert not errors, errors
        assert not external, external
        assert all(not any(secret in body for secret in ('fixture-read', GATEWAY, 'm=video ', 'data:image'))
                   for body in payloads)
        result = {'passed': True, 'view': VIEW, 'gateway': 'MediaMTX v1.21.2', 'input': 'FFmpeg synthetic RTSP H264',
                  'decoded_frames': frames, 'audio_tracks': 0, 'real_https_whep': True,
                  'session_delete_sent': True, 'gateway_inventory_empty_after_stop': True,
                  'policy_revocation': True, 'timer_survives_stop': True,
                  'gateway_response_statuses': gateway_responses,
                  'gateway_credentials_reach_app': False, 'unexpected_external_attempts': external,
                  'blocked_optional_font_attempts': len(blocked_optional_fonts), 'page_errors': errors}
        (OUT / 'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        browser.close()
finally:
    server.shutdown()
    server.server_close()
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    for log in logs:
        log.close()
