const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/study-camera-network.js'), 'utf8');
const SDP = 'v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=sendonly\r\n';
const GATEWAY = 'https://gateway.test/study/whep';

function harness(handler) {
  const requests = [];
  const tracks = [{ kind: 'video', stopped: false, stop() { this.stopped = true; } }];
  const stream = { getTracks: () => tracks };
  class PC {
    constructor(config) { this.config = config; this.iceGatheringState = 'complete'; }
    addTransceiver(kind, options) { assert.equal(kind, 'video'); assert.equal(options.direction, 'recvonly'); }
    async createOffer() { return { type: 'offer', sdp: SDP }; }
    async setLocalDescription(value) { this.localDescription = value; }
    addEventListener() {}
    removeEventListener() {}
    async setRemoteDescription(value) {
      this.remoteDescription = value;
      this.ontrack({ track: tracks[0], streams: [stream] });
      this.connectionState = 'connected';
      this.onconnectionstatechange();
    }
    close() { this.closed = true; }
  }
  const win = { location: { origin: 'https://intelliplan.test' }, RTCPeerConnection: PC,
    setTimeout, clearTimeout };
  const context = vm.createContext({ window: win, URL, TextEncoder, TextDecoder, AbortController,
    btoa, fetch: async (url, options) => {
      requests.push({ url, options });
      if (handler) {
        const response = await handler(url, options);
        if (response) return response;
      }
      if (url === '/api/active/focus-permission') return Response.json({ allowed: true });
      if (options.method === 'DELETE') return new Response(null, { status: 204 });
      return new Response(SDP, { status: 201, headers: {
        'Content-Type': 'application/sdp', Location: '/study/whep/session1' } });
    }
  });
  vm.runInContext(source, context);
  return { api: win.IPNetworkCamera, requests, tracks, stream };
}

test('receives video without local capture, cookies, redirects or external ICE servers', async () => {
  const h = harness();
  const camera = new h.api.Network({ url: GATEWAY, password: 'camera-token' });
  assert.equal(await camera.start(), h.stream);
  const post = h.requests.find(r => r.options.method === 'POST');
  assert.equal(post.options.credentials, 'omit');
  assert.equal(post.options.redirect, 'error');
  assert.equal(post.options.referrerPolicy, 'no-referrer');
  assert.equal(post.options.headers.Authorization, 'Bearer camera-token');
  assert.deepEqual(Array.from(camera.pc.config.iceServers), []);
  assert.ok(h.requests.filter(r => r.url.startsWith('/api/')).every(r => !r.options.headers));
  camera.stop();
  await new Promise(setImmediate);
  assert.equal(h.tracks[0].stopped, true);
  assert.ok(h.requests.some(r => r.options.method === 'DELETE' && r.url === 'https://gateway.test/study/whep/session1'));
});

for (const url of ['rtsp://camera/study', 'http://camera/study/whep', 'https://u:p@camera/whep',
  'https://camera/whep#secret', 'https://intelliplan.test/api/write', 'invalid']) {
  test('rejects unsupported or unsafe endpoint before contacting it: ' + url.split('@').at(-1), async () => {
    const h = harness();
    const camera = new h.api.Network({ url });
    await assert.rejects(camera.start());
    assert.equal(h.requests.length, 0);
  });
}

test('basic gateway credentials never reach the application policy endpoint', async () => {
  const h = harness();
  const camera = new h.api.Network({ url: GATEWAY, username: 'viewer', password: 'pass' });
  await camera.start();
  const post = h.requests.find(r => r.options.method === 'POST');
  assert.equal(post.options.headers.Authorization, 'Basic ' + btoa('viewer:pass'));
  camera.stop();
});

test('refuses a cross-origin session Location without forwarding credentials', async () => {
  const h = harness((url, options) => options.method === 'POST'
    ? new Response(SDP, { status: 201, headers: { 'Content-Type': 'application/sdp', Location: 'https://other.test/session' } }) : null);
  await assert.rejects(new h.api.Network({ url: GATEWAY, password: 'private' }).start());
  assert.equal(h.requests.some(r => r.url.includes('other.test')), false);
});

test('an oversized streamed answer is bounded and its session is deleted', async () => {
  const h = harness((url, options) => options.method === 'POST'
    ? new Response(SDP + 'x'.repeat(70000), { status: 201, headers: {
      'Content-Type': 'application/sdp', Location: '/study/whep/session1' } }) : null);
  await assert.rejects(new h.api.Network({ url: GATEWAY }).start(), /oversized/);
  assert.ok(h.requests.some(r => r.options.method === 'DELETE'));
});

test('audio answers are rejected and cleaned up', async () => {
  const h = harness((url, options) => options.method === 'POST'
    ? new Response(SDP + 'm=audio 9 RTP 0\r\n', { status: 201, headers: {
      'Content-Type': 'application/sdp', Location: '/study/whep/session1' } }) : null);
  await assert.rejects(new h.api.Network({ url: GATEWAY }).start(), /video-only/);
  assert.ok(h.requests.some(r => r.options.method === 'DELETE'));
});

test('a late POST after cancellation is deleted and cannot reopen video', async () => {
  let deliver;
  const h = harness((url, options) => options.method === 'POST' ? new Promise(resolve => { deliver = resolve; }) : null);
  const camera = new h.api.Network({ url: GATEWAY });
  const pending = camera.start();
  const rejected = assert.rejects(pending, /stopped/);
  while (!deliver) await new Promise(setImmediate);
  camera.stop();
  deliver(new Response(SDP, { status: 201, headers: {
    'Content-Type': 'application/sdp', Location: '/study/whep/late' } }));
  await rejected;
  await new Promise(setImmediate);
  assert.ok(h.requests.some(r => r.options.method === 'DELETE' && r.url.endsWith('/late')));
  assert.equal(camera.stream, null);
});

test('fresh policy denial fails closed before contacting the camera', async () => {
  const h = harness(url => url.startsWith('/api/') ? Response.json({ allowed: false }) : null);
  await assert.rejects(new h.api.Network({ url: GATEWAY }).start(), /current settings/);
  assert.equal(h.requests.some(r => r.url === GATEWAY), false);
});
