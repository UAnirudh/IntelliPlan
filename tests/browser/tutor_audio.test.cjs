const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/tutor.html'), 'utf8');
const audioSource = source.slice(source.indexOf('let ttsVoiceId = null;'), source.indexOf('/* ════ ADAPTIVE STUDENT MODEL'));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
function harness(fetch, options = {}) {
  const played = [], fallback = [], revoked = [], audios = [];
  const utterances = [], elements = { tutorAudioFeedback: { textContent: '' }, tutorAudioPlay: { hidden: true } };
  const speech = { cancel() {}, speak(value) { fallback.push(value.text); utterances.push(value); } };
  const request = (url, init) => url === '/api/tutor/voices'
    ? options.capabilities || Promise.resolve({ok: true, json: async () => ({voices: [], speech_max_chars: 200})})
    : fetch(url, init);
  const context = vm.createContext({ fetch: request, AbortController, ttsEnabled: true, _ttsVoice: null,
    setTimeout, clearTimeout,
    document: { getElementById: id => elements[id] || null },
    localStorage: { getItem: () => null }, window: { speechSynthesis: speech }, speechSynthesis: speech,
    SpeechSynthesisUtterance: function (text) { this.text = text; },
    URL: { createObjectURL: b => b.id, revokeObjectURL: id => revoked.push(id) },
    Audio: function (url) { this.url = url; audios.push(this); this.play = async () => {
      if (options.play) await options.play();
      played.push(url);
    }; this.pause = () => {}; },
  });
  vm.runInContext(audioSource + '\nglobalThis.api = {speakReply,stopSpeaking,splitSpeech,setTtsVoice};', context);
  return { api: context.api, played, fallback, revoked, audios, elements, utterances };
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

test('audio decode errors release the blob and fall back instead of reporting finished', async () => {
  const h = harness(async () => ({ ok: true, blob: async () => ({ id: 'broken' }) }));
  await h.api.speakReply('Read me'); h.audios[0].onerror();
  assert.deepEqual(h.fallback, ['Read me']); assert.ok(h.revoked.includes('broken'));
  assert.match(h.elements.tutorAudioFeedback.textContent, /browser voice/);
});

test('stale audio errors and browser utterance events cannot affect a newer reply', async () => {
  let n = 0;
  const h = harness(async () => ++n === 1 ? {ok: false, status: 503} : {ok: true, blob: async () => ({id: 'new'})});
  await h.api.speakReply('old'); const old = h.utterances[0];
  await h.api.speakReply('new'); old.onend(); old.onerror();
  assert.equal(h.elements.tutorAudioFeedback.textContent, 'Reading aloud.');
  await h.api.speakReply('newer'); h.audios[0].onerror();
  assert.deepEqual(h.fallback, ['old']);
});

test('autoplay blocked audio offers a gesture button that retries and stop clears it', async () => {
  let tries = 0;
  const h = harness(async () => ({ok: true, blob: async () => ({id: 'tap'})}), {play: async () => {
    if (++tries === 1) throw Object.assign(new Error('gesture'), {name: 'NotAllowedError'});
  }});
  await h.api.speakReply('tap to read');
  assert.equal(h.elements.tutorAudioPlay.hidden, false); assert.deepEqual(h.fallback, []);
  await h.elements.tutorAudioPlay.onclick();
  assert.deepEqual(h.played, ['tap']); assert.equal(h.elements.tutorAudioPlay.hidden, true);
  h.api.stopSpeaking(); assert.equal(h.elements.tutorAudioPlay.onclick, null);
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

test('long replies are read completely in ordered provider-sized chunks', async () => {
  const sent = [];
  const text = ('Explain each step clearly. Then check your prediction. ').repeat(35) + 'Final sentence.';
  const h = harness(async (url, options) => {
    sent.push(JSON.parse(options.body).text);
    return {ok: true, blob: async () => ({id: String(sent.length)})};
  });
  await h.api.speakReply(text);
  for (let i = 0; i < 30 && h.elements.tutorAudioFeedback.textContent !== 'Finished reading.'; i++) {
    h.audios.at(-1).onended(); await new Promise(setImmediate);
  }
  assert.equal(h.elements.tutorAudioFeedback.textContent, 'Finished reading.');
  assert.ok(sent.length > 1 && sent.every(part => part.length <= 200));
  assert.equal(sent.join(' '), text.replace(/\s+/g, ' ').trim());
  assert.deepEqual(h.fallback, []);
});

test('stop during a later chunk request prevents its playback and any further requests', async () => {
  const later = deferred(); let requests = 0;
  const h = harness(async () => ++requests === 1 ? {ok: true, blob: async () => ({id: 'first'})} : later.promise);
  await h.api.speakReply('A sentence to read. '.repeat(50));
  h.audios[0].onended(); h.api.stopSpeaking();
  later.resolve({ok: true, blob: async () => ({id: 'stale-later'})}); await new Promise(setImmediate);
  assert.equal(requests, 2); assert.deepEqual(h.played, ['first']); assert.deepEqual(h.fallback, []);
});

test('policy denial on a later chunk stops the queue without browser fallback', async () => {
  let requests = 0;
  const h = harness(async () => ++requests === 1 ? {ok: true, blob: async () => ({id: 'first'})} : {ok: false, status: 403});
  await h.api.speakReply('A sentence to read. '.repeat(50));
  h.audios[0].onended(); await new Promise(setImmediate);
  assert.equal(requests, 2); assert.deepEqual(h.fallback, []);
  assert.match(h.elements.tutorAudioFeedback.textContent, /school settings/);
});

test('changing reading voice stops the current queue before later chunks', async () => {
  let requests = 0;
  const h = harness(async () => ({ok: true, blob: async () => ({id: String(++requests)})}));
  await h.api.speakReply('A sentence to read. '.repeat(50));
  h.api.setTtsVoice('new-voice'); h.audios[0].onended(); await new Promise(setImmediate);
  assert.equal(requests, 1); assert.deepEqual(h.fallback, []);
});

test('speech chunk boundaries preserve all source characters and Unicode pairs', () => {
  const h = harness(async () => ({}));
  const text = 'x'.repeat(199) + '🧠' + ' Long words and a final sentence. '.repeat(30);
  const chunks = h.api.splitSpeech(text, 200);
  assert.equal(chunks.join(''), text);
  assert.ok(chunks.every(part => part.length <= 200 && !/[\uD800-\uDBFF]$/.test(part)));
});

test('an immediate first read waits for server speech limits rather than spending a request per 200 characters', async () => {
  const capabilities = deferred(), sent = [];
  const h = harness(async (url, options) => {
    sent.push(JSON.parse(options.body).text);
    return {ok: true, blob: async () => ({id: 'one-action'})};
  }, {capabilities: capabilities.promise});
  const pending = h.api.speakReply('A complete explanation. '.repeat(60));
  await new Promise(setImmediate); assert.deepEqual(sent, []);
  capabilities.resolve({ok: true, json: async () => ({voices: [], speech_max_chars: 2000, speech_timeout_ms: 45000})});
  await pending; assert.equal(sent.length, 1); assert.ok(sent[0].length > 1200);
  h.api.stopSpeaking();
});
