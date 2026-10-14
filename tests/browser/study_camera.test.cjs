const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/ip-focus.js'), 'utf8');

function stream() {
  const track = { stopped: false, onended: null, stop() { this.stopped = true; } };
  return { track, getTracks: () => [track], getVideoTracks: () => [track] };
}

function harness(acquire = async () => stream()) {
  const calls = [];
  const timers = new Set();
  const events = [];
  let clock = Date.now();
  class Clock extends Date { static now() { return clock; } }
  const doc = { hidden: false, hasFocus: () => true,
    addEventListener() {}, removeEventListener() {},
    createElement(type) {
      return type === 'video' ? { play: async () => {}, readyState: 2 }
        : { getContext: () => ({}) };
    } };
  const media = { getUserMedia(options) { calls.push(options); return acquire(options); },
    enumerateDevices: async () => [] };
  const win = { setInterval(callback) { timers.add(callback); return callback; },
    clearInterval(callback) { timers.delete(callback); }, addEventListener() {}, removeEventListener() {} };
  const context = vm.createContext({ window: win, document: doc, navigator: { mediaDevices: media },
    Date: Clock, performance: { now: () => clock } });
  vm.runInContext(source, context);
  return { api: win.IPFocus, calls, timers, media, events, advance: ms => { clock += ms; },
    tracker: () => new win.IPFocus.Tracker({ onCameraStatus: info => events.push(info) }) };
}

test('lists video inputs only, including USB, capture and virtual cameras', async () => {
  const h = harness();
  h.media.enumerateDevices = async () => [
    { kind: 'audioinput', deviceId: 'mic', label: 'Microphone' },
    ...['USB webcam', 'Capture card', 'Phone virtual webcam'].map((label, i) =>
      ({ kind: 'videoinput', deviceId: String(i), label })),
  ];
  assert.deepEqual(Array.from(await h.api.cameras(), d => d.label),
    ['USB webcam', 'Capture card', 'Phone virtual webcam']);
  assert.equal(h.calls.length, 0, 'listing must never open a camera');
});

test('explicit device is exact and never requests a microphone', async () => {
  const h = harness();
  const camera = new h.api.CameraMonitor();
  assert.equal(await camera.start({ deviceId: 'capture-card' }), true);
  assert.equal(h.calls[0].video.deviceId.exact, 'capture-card');
  assert.equal(h.calls[0].audio, false);
  assert.equal(h.calls[0].video.facingMode, undefined);
  camera.stop();
});

test('phone back-camera preference remains usable without device labels', async () => {
  const h = harness();
  const camera = new h.api.CameraMonitor();
  await camera.start({ facingMode: 'environment' });
  assert.equal(h.calls[0].video.facingMode.ideal, 'environment');
  camera.stop();
});

test('cancelling a pending permission request closes its eventual stream', async () => {
  let resolve;
  const h = harness(() => new Promise(done => { resolve = done; }));
  const camera = new h.api.CameraMonitor();
  const pending = camera.start();
  await Promise.resolve();
  camera.stop();
  const late = stream();
  resolve(late);
  assert.equal(await pending, false);
  assert.equal(late.track.stopped, true);
  assert.equal(h.api.activeCamera(), null);
});

test('a stale stream cannot replace a newer selected device', async () => {
  const resolves = [];
  const h = harness(() => new Promise(done => resolves.push(done)));
  const camera = new h.api.CameraMonitor();
  const first = camera.start({ deviceId: 'first' });
  await Promise.resolve();
  const second = camera.start({ deviceId: 'second' });
  await Promise.resolve();
  const current = stream();
  resolves[1](current);
  assert.equal(await second, true);
  const stale = stream();
  resolves[0](stale);
  assert.equal(await first, false);
  assert.equal(stale.track.stopped, true);
  assert.equal(current.track.stopped, false);
  assert.equal(camera.stream, current);
  camera.stop();
});

test('stopping a pending tracker cannot revive its timer or camera', async () => {
  let resolve;
  const h = harness(() => new Promise(done => { resolve = done; }));
  const tracker = h.tracker();
  const pending = tracker.start(true);
  await Promise.resolve();
  tracker.stop();
  const late = stream();
  resolve(late);
  assert.equal(await pending, false);
  assert.equal(h.timers.size, 0);
  assert.equal(late.track.stopped, true);
  assert.equal(h.events.filter(event => event.ok).length, 0);
});

test('switching cameras preserves session summaries and stops the old tracks', async () => {
  const h = harness();
  const tracker = h.tracker();
  await tracker.start(true, { deviceId: 'usb' });
  const old = tracker.camera.stream;
  tracker.summary.samples = 17;
  await tracker.selectCamera({ deviceId: 'capture' });
  assert.equal(old.track.stopped, true);
  assert.equal(tracker.summary.samples, 17);
  assert.equal(h.calls[1].video.deviceId.exact, 'capture');
  assert.equal(h.timers.size, 1);
  tracker.stop();
  assert.equal(h.timers.size, 0);
});

test('disconnection stops capture and explicitly reports the loss', async () => {
  const h = harness();
  const tracker = h.tracker();
  await tracker.start(true);
  const captured = tracker.camera.stream;
  captured.track.onended();
  assert.equal(captured.track.stopped, true);
  assert.equal(tracker.camera, null);
  assert.equal(h.api.activeCamera(), null);
  assert.equal(h.events.at(-1).reason, 'disconnected');
  tracker.stop();
});

test('a missing exact camera fails without silently opening another camera', async () => {
  const h = harness(async () => { throw Object.assign(new Error('missing'), { name: 'OverconstrainedError' }); });
  const tracker = h.tracker();
  await tracker.start(true, { deviceId: 'missing' });
  assert.equal(h.calls.length, 1);
  assert.equal(tracker.camera, null);
  assert.equal(h.events.at(-1).reason, 'OverconstrainedError');
  tracker.stop();
});

test('a declined camera can be retried on the existing study tracker', async () => {
  let allowed = false;
  const h = harness(async () => {
    if (!allowed) throw Object.assign(new Error('denied'), { name: 'NotAllowedError' });
    return stream();
  });
  const tracker = h.tracker();
  await tracker.start(true);
  assert.equal(h.events.at(-1).reason, 'NotAllowedError');
  allowed = true;
  assert.equal(await tracker.selectCamera({ deviceId: 'usb' }), true);
  assert.equal(h.events.at(-1).ok, true);
  tracker.stop();
});

test('stale camera samples are discarded after stopping', async () => {
  const h = harness();
  const tracker = h.tracker();
  await tracker.start(true);
  let resolve;
  tracker.camera.sample = () => new Promise(done => { resolve = done; });
  tracker._tick();
  tracker.stop();
  resolve({ state: 'present', confidence: 1 });
  await Promise.resolve();
  assert.equal(tracker.summary.samples, 0);
});

test('pause and resume preserve counts, offsets and separate streaks', async () => {
  const h = harness();
  const buckets = [];
  const tracker = new h.api.Tracker({ onBucket: bucket => buckets.push(bucket) });
  await tracker.start(true);
  for (let i = 0; i < 8; i++) {
    tracker._record({ state: 'present', confidence: 1 });
    h.advance(2000);
  }
  const before = tracker.stop();
  const firstOffset = buckets.at(-1).offset_seconds;
  const longest = before.longest_streak_seconds;
  h.advance(120000);
  assert.equal(tracker.snapshot().longest_streak_seconds, longest, 'pause must not extend a streak');
  await tracker.start(true);
  tracker._record({ state: 'present', confidence: 1 });
  const after = tracker.stop();
  assert.equal(after.samples, 9);
  assert.equal(after.present, 9);
  assert.ok(buckets.at(-1).offset_seconds > firstOffset + 100);
  assert.equal(after.longest_streak_seconds, longest);
});

test('a restored tracker seeds exact saved totals without opening a camera', () => {
  const h = harness();
  const tracker = new h.api.Tracker({ summary: {
    samples: 30, present: 20, away: 5, absent: 5, longest_streak_seconds: 42,
  } });
  const summary = tracker.snapshot();
  assert.equal(summary.samples, 30);
  assert.equal(summary.present, 20);
  assert.equal(summary.longest_streak_seconds, 42);
  assert.equal(h.calls.length, 0);
});
