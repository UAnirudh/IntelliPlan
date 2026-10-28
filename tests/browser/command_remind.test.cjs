const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(path.join(root, 'static/js/command_remind.js'), 'utf8');

function harness({decided = false} = {}) {
  const calls = [];
  const handlers = {};
  const storage = new Map(decided ? [['ip_remind_prompt_dismissed_42', '1']] : []);
  const text = {textContent: '', parentNode: {dataset: {}}};
  const summary = {textContent: 'Reminders'};
  const strong = {textContent: 'Set up reminders'};
  const on = {hidden: false, disabled: false, textContent: 'Enable',
    addEventListener(name, fn) { handlers['on:' + name] = fn; }};
  const dismiss = {textContent: 'Not now',
    addEventListener(name, fn) { handlers['dismiss:' + name] = fn; }};
  const bar = {
    dataset: {userId: '42'}, hidden: true, open: false,
    addEventListener(name, fn) { handlers[name] = fn; },
    querySelector(selector) { return selector === 'strong' ? strong : summary; },
  };
  const document = {getElementById(id) {
    return ({commandRemind: bar, commandRemindText: text,
      commandRemindOn: on, commandRemindDismiss: dismiss})[id] || null;
  }};
  const localStorage = {
    getItem(key) { return storage.get(key) || null; },
    setItem(key, value) { storage.set(key, value); },
  };
  const IPReminders = {
    pushSupported() { return true; },
    pushUnavailableReason() { return ''; },
    enable(options, overrides) {
      calls.push(['enable', options, overrides]);
      return Promise.resolve({saved: true, push: true});
    },
  };
  const Notification = {
    permission: 'default',
    requestPermission() {
      calls.push(['permission']);
      return Promise.resolve('granted');
    },
  };
  vm.runInNewContext(script, {document, window: {IPReminders}, IPReminders,
    Notification, localStorage, Promise});
  return {bar, calls, handlers, on, text, dismiss, storage};
}

test('opens the reminder explanation on arrival without requesting browser permission', () => {
  const h = harness();
  assert.equal(h.bar.hidden, false);
  assert.equal(h.bar.open, true);
  assert.equal(h.calls.length, 0);
});

test('requests browser permission synchronously only after Enable is tapped', async () => {
  const h = harness();
  assert.equal(h.calls.length, 0);

  h.handlers['on:click'].call(null, {target: h.on});
  assert.deepEqual(h.calls.map((call) => call[0]), ['permission', 'enable']);
  assert.equal(h.calls[1][2].permissionRequest instanceof Promise, true);

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.storage.get('ip_remind_prompt_dismissed_42'), '1');
  assert.equal(h.on.hidden, true);
  assert.equal(h.bar.querySelector('strong').textContent, 'Reminders are on.');
});

test('a saved reminder decision suppresses the automatic prompt', () => {
  const h = harness({decided: true});
  assert.equal(h.bar.hidden, true);
  assert.equal(h.bar.open, false);
  assert.equal(h.calls.length, 0);
});
