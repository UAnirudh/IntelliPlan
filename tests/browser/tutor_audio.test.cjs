const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/tutor.html'), 'utf8');
const audioSource = source.slice(source.indexOf('let ttsVoiceId = null;'), source.indexOf('/* ════ ADAPTIVE STUDENT MODEL'));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
function harness(fetch) {
  const played = [], fallback = [], revoked = [], audios = [];
  const speech = { cancel() {}, speak(value) { fallback.push(value.text); } };
  const context = vm.createContext({ fetch, AbortController, ttsEnabled: true, _ttsVoice: null,
    document: { getElementById: () => null },
    localStorage: { getItem: () => null }, window: { speechSynthesis: speech }, speechSynthesis: speech,
    SpeechSynthesisUtterance: function (text) { this.text = text; },
    URL: { createObjectURL: b => b.id, revokeObjectURL: id => revoked.push(id) },
    Audio: function (url) { this.url = url; audios.push(this); this.play = async () => played.push(url); this.pause = () => {}; },
  });
  vm.runInContext(audioSource + '\nglobalThis.api = {speakReply,stopSpeaking};', context);
  return { api: context.api, played, fallback, revoked, audios };
}
test('stop while speech request is pending cannot start stale audio or fallback', async () => {
  const wait = deferred(); const h = harness(() => wait.promise);
  const pending = h.api.speakReply('Old question?'); h.api.stopSpeaking();
  wait.resolve({ ok: true, blob: async () => ({ id: 'old' }) }); await pending;
  assert.deepEqual(h.played, []); assert.deepEqual(h.fallback, []);
});
test('new speech supersedes an older delayed blob', async () => {
  const blob = deferred(), started = deferred(); let count = 0;
  const h = harness(async () => ({ ok: true, blob: () => {
    if (++count === 1) { started.resolve(); return blob.promise; }
    return Promise.resolve({ id: 'new' });
  } }));
  const old = h.api.speakReply('Old question?'); await started.promise;
  await h.api.speakReply('New question?'); blob.resolve({ id: 'old' }); await old;
  assert.deepEqual(h.played, ['new']); assert.deepEqual(h.fallback, []);
});
test('stop releases the active audio URL, old end events do not clear newer playback', async () => {
  let n = 0; const h = harness(async () => ({ ok: true, blob: async () => ({ id: String(++n) }) }));
  await h.api.speakReply('One?'); const old = h.audios[0];
  await h.api.speakReply('Two?'); old.onended(); h.api.stopSpeaking();
  assert.ok(h.revoked.includes('1')); assert.ok(h.revoked.includes('2'));
});
test('a current provider error falls back to browser speech', async () => {
  const h = harness(async () => ({ ok: false, status: 503 }));
  await h.api.speakReply('What next?'); assert.deepEqual(h.fallback, ['What next?']);
});
test('explicit format selection turns voice on for auditory and off for reading', async () => {
  const start = source.indexOf('let _modalitySaveQueue =');
  const end = source.indexOf('/* Close-out:', start);
  let stops = 0, renders = 0;
  const context = vm.createContext({ modalityMode: 'auto', modalityWeights: null, ttsEnabled: false,
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    localStorage: { setItem() {} }, renderModalitySwitcher() {},
    _renderTtsState() { renders++; }, stopSpeaking() { stops++; },
  });
  vm.runInContext(source.slice(start, end) + '\nglobalThis.set = setModality;', context);
  await context.set('auditory'); assert.equal(context.ttsEnabled, true);
  await context.set('reading'); assert.equal(context.ttsEnabled, false);
  assert.equal(stops, 1); assert.equal(renders, 2);
});
test('policy-denied speech does not bypass the denial with browser fallback', async () => {
  const h = harness(async () => ({ ok: false, status: 403 }));
  await h.api.speakReply('Question?'); assert.deepEqual(h.fallback, []);
});
test('rapid format changes save in order and only the latest changes voice', async () => {
  const start = source.indexOf('let _modalitySaveQueue =');
  const end = source.indexOf('/* Close-out:', start);
  const first = deferred(), calls = [];
  const context = vm.createContext({ modalityMode: 'auto', modalityWeights: null, ttsEnabled: false,
    fetch: async (url, options) => {
      calls.push(JSON.parse(options.body).mode);
      return calls.length === 1 ? first.promise : { ok: true, json: async () => ({}) };
    }, localStorage: { setItem() {} }, renderModalitySwitcher() {}, _renderTtsState() {}, stopSpeaking() {},
  });
  vm.runInContext(source.slice(start, end) + '\nglobalThis.set = setModality;', context);
  const a = context.set('auditory'), b = context.set('reading');
  await Promise.resolve(); await Promise.resolve();
  assert.deepEqual(calls, ['auditory']);
  first.resolve({ ok: true, json: async () => ({}) }); await Promise.all([a, b]);
  assert.deepEqual(calls, ['auditory', 'reading']);
  assert.equal(context.modalityMode, 'reading'); assert.equal(context.ttsEnabled, false);
});
