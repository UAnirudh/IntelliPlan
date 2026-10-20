const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const template = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/tutor.html'), 'utf8');
const source = template.slice(template.indexOf('async function recordAndTranscribe()'), template.indexOf('// ── TTS ──'));
function harness(options = {}) {
  let requests = 0, tracksStopped = 0, nativeRecognition = 0;
  const recorders = [], sent = [], alerts = [], attributes = {}, timers = [];
  const button = {disabled: false, classList: {add() {}, remove() {}}, setAttribute(k, v) {attributes[k] = v;}};
  const input = {value: '', disabled: false, focus() {}};
  const send = {disabled: false};
  const stream = {getTracks: () => [{stop() {tracksStopped++;}}]};
  const MediaRecorder = function (value) {
    if (options.constructorFails) throw new Error('unsupported format');
    this.stream = value; this.state = 'inactive'; this.mimeType = options.mime || 'audio/mp4'; recorders.push(this);
    this.start = () => {this.state = 'recording'; this.onstart();};
    this.stop = () => {
      this.state = 'inactive'; this.ondataavailable({data: new Blob(['synthetic recording'], {type: this.mimeType})});
      this.done = this.onstop();
    };
  };
  const context = vm.createContext({
    window: {MediaRecorder, SpeechRecognition: function () {nativeRecognition++;}}, MediaRecorder, Blob, FormData,
    navigator: {mediaDevices: {getUserMedia() {requests++; return options.permission || Promise.resolve(stream);}}},
    document: {getElementById: id => ({micBtn: button, tutorInput: input, tutorSendBtn: send})[id]},
    IP: {alert: message => alerts.push(message)}, isProcessing: false,
    autoResizeTutor() {}, setAudioStatus() {}, stopSpeaking() {}, AbortController,
    setTimeout(callback, ms) {timers.push({callback, ms}); return timers.length;}, clearTimeout() {},
    fetch: async (url, init) => {sent.push({url, file: init.body.get('file')}); return options.request ? options.request(init) : {ok: true, json: async () => ({text: 'My recorded question.'})};},
  });
  vm.runInContext(source + '\nglobalThis.api={start:recordAndTranscribe,toggle:toggleVoice,cancel:cancelVoiceInput,deactivate(){voicePageActive=false;},state(){return {microphoneStarting,transcriptionPending,isRecording};}};', context);
  return {api: context.api, recorders, sent, alerts, button, input, attributes, stream, timers,
    stats: () => ({requests, tracksStopped, nativeRecognition})};
}

test('MP4 recordings use m4a extension, authorized endpoint, and review-before-send transcript', async () => {
  const h = harness(); await h.api.toggle(); await h.api.toggle(); await h.recorders[0].done;
  assert.equal(h.sent[0].url, '/api/tutor/voice');
  assert.equal(h.sent[0].file.name, 'speech.m4a'); assert.equal(h.sent[0].file.type, 'audio/mp4');
  assert.equal(h.input.value, 'My recorded question.'); assert.equal(h.input.disabled, false);
  assert.equal(h.stats().tracksStopped, 1); assert.equal(h.stats().nativeRecognition, 0);
  assert.equal(h.attributes['aria-pressed'], 'false');
});

test('repeated mic clicks while permission is pending request only one stream', async () => {
  let resolve; const permission = new Promise(r => {resolve = r;}); const h = harness({permission});
  const first = h.api.start(); await h.api.start(); assert.equal(h.stats().requests, 1);
  resolve(h.stream); await first; assert.equal(h.recorders.length, 1);
  await h.api.start(); await h.recorders[0].done; assert.equal(h.stats().tracksStopped, 1);
});

test('MediaRecorder construction failure releases microphone tracks and permits retry', async () => {
  const h = harness({constructorFails: true}); await h.api.start();
  assert.equal(h.stats().tracksStopped, 1); assert.equal(h.button.disabled, false);
  assert.equal(h.api.state().microphoneStarting, false); assert.match(h.alerts[0], /could not start recording/);
});

test('permission resolving after navigation releases stream without recording or uploading', async () => {
  let resolve; const permission = new Promise(r => {resolve = r;}); const h = harness({permission});
  const first = h.api.start(); h.api.deactivate(); resolve(h.stream); await first;
  assert.equal(h.stats().tracksStopped, 1); assert.equal(h.recorders.length, 0); assert.equal(h.sent.length, 0);
});

test('a transcript arriving after a chat change cannot append to the new draft', async () => {
  let resolve; const request = new Promise(r => {resolve = r;}); const h = harness({request: () => request});
  await h.api.start(); await h.api.start();
  h.api.cancel(); h.input.value = 'New chat draft';
  resolve({ok: true, json: async () => ({text: 'Old chat transcript'})}); await h.recorders[0].done;
  assert.equal(h.input.value, 'New chat draft'); assert.equal(h.input.disabled, false);
  assert.equal(h.api.state().transcriptionPending, false);
});

test('transcription timeout aborts the request and always restores typing', async () => {
  const h = harness({request: init => new Promise((resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(Object.assign(new Error('timeout'), {name: 'AbortError'})));
  })});
  await h.api.start(); await h.api.start();
  assert.equal(h.input.disabled, true);
  h.timers.find(timer => timer.ms === 30000).callback(); await h.recorders[0].done;
  assert.equal(h.input.disabled, false); assert.equal(h.button.disabled, false);
  assert.equal(h.api.state().transcriptionPending, false);
});
