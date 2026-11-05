const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const reminderScript = fs.readFileSync(path.join(root, 'static/js/ip-reminders.js'), 'utf8');
const logoutScript = fs.readFileSync(path.join(root, 'static/js/ip-logout-push.js'), 'utf8');

function remindersFor({userAgent, platform, touchPoints = 0, standalone = false,
  worker = true, push = true, notification = true}) {
  const window = { matchMedia: () => ({matches: standalone}) };
  if (push) window.PushManager = function PushManager() {};
  const navigator = {userAgent, platform, maxTouchPoints: touchPoints};
  if (worker) navigator.serviceWorker = {};
  if (notification) window.Notification = function Notification() {};
  vm.runInNewContext(reminderScript, {window, navigator, setTimeout, console});
  return window.IPReminders;
}

test('iOS Safari tabs do not offer push before Home Screen installation', () => {
  const reminders = remindersFor({userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)'});
  assert.equal(reminders.pushSupported(), false);
  assert.equal(reminders.pushUnavailableReason(), 'ios-home-screen');
});

test('installed iOS web apps can use push when browser APIs are available', () => {
  const reminders = remindersFor({
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)', standalone: true,
  });
  assert.equal(reminders.pushSupported(), true);
  assert.equal(reminders.pushUnavailableReason(), '');
});

test('iPadOS desktop user agent still requires Home Screen installation', () => {
  const reminders = remindersFor({userAgent: 'Mozilla/5.0 (Macintosh)', platform: 'MacIntel', touchPoints: 5});
  assert.equal(reminders.pushUnavailableReason(), 'ios-home-screen');
});

test('unsupported browsers retain the generic unsupported reason', () => {
  const reminders = remindersFor({userAgent: 'Desktop browser', worker: false});
  assert.equal(reminders.pushUnavailableReason(), 'unsupported');
});

test('logout removes and unsubscribes only the current browser endpoint before submitting', async () => {
  let submitHandler;
  let serverRequest;
  let prevented = 0;
  const sequence = [];
  class HTMLFormElement {
    constructor() { this.action = '/logout'; this.dataset = {}; }
    requestSubmit() { submitHandler({target: this, preventDefault() { prevented += 1; }}); }
    submit() { sequence.push('logout'); }
  }
  const subscription = {
    endpoint: 'https://push.example.test/device-a',
    async unsubscribe() { sequence.push('unsubscribe'); return true; },
  };
  const navigator = {serviceWorker: {
    async getRegistration() { return {pushManager: {async getSubscription() { return subscription; }}}; },
  }};
  const window = {location: {href: 'https://intelliplan.tech/dashboard'}, PushManager: function PushManager() {}, addEventListener() {}};
  const storage = new Map();
  const localStorage = {
    getItem(key) { return storage.get(key) || null; },
    setItem(key, value) { storage.set(key, value); },
  };
  const document = {body: {dataset: {userId: '42'}}, addEventListener(name, handler) { if (name === 'submit') submitHandler = handler; }};
  const fetch = async (url, options) => { serverRequest = {url, options}; sequence.push('server'); return {ok: true}; };
  vm.runInNewContext(logoutScript, {document, window, navigator, fetch, localStorage, HTMLFormElement, URL, Event, Promise, setTimeout, clearTimeout});
  const form = new HTMLFormElement();
  window.IPLogoutPush.submit(form);
  form.requestSubmit();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(prevented, 2);
  assert.equal(serverRequest.url, '/push/unsubscribe');
  assert.equal(JSON.parse(serverRequest.options.body).endpoint, subscription.endpoint);
  assert.deepEqual(sequence, ['server', 'unsubscribe', 'logout']);
  assert.equal(storage.get('ip_push_restore_user_42'), '1');
});

test('a prior reminder opt-in is restored on this account after the next sign-in', async () => {
  const storage = new Map([['ip_push_restore_user_42', '1']]);
  const calls = [];
  const registration = {pushManager: {
    async getSubscription() { return null; },
    async subscribe() {
      return {toJSON() { return {endpoint: 'https://push.example.test/device-a', keys: {}}; }};
    },
  }};
  const navigator = {
    userAgent: 'Desktop browser', platform: 'Win32', maxTouchPoints: 0,
    serviceWorker: {ready: Promise.resolve(registration)},
  };
  const window = {PushManager: function PushManager() {}, Notification: {permission: 'granted'}};
  const localStorage = {
    getItem(key) { return storage.get(key) || null; },
    removeItem(key) { storage.delete(key); },
  };
  const fetch = async (url, options) => {
    calls.push(url);
    if (url === '/api/notifications/preferences') return {ok: true, async json() { return {channels: ['email', 'push']}; }};
    if (url === '/push/vapid-public') return {ok: true, async json() { return {key: 'AA'}; }};
    if (url === '/push/subscribe') return {ok: true};
    throw new Error(`Unexpected request ${url}`);
  };
  vm.runInNewContext(reminderScript, {window, navigator, Notification: window.Notification,
    localStorage, fetch, atob, setTimeout, console, Uint8Array, Promise});
  assert.equal(await window.IPReminders.restoreSavedBrowserPush('42'), true);
  assert.equal(storage.has('ip_push_restore_user_42'), false);
  assert.deepEqual(calls, ['/api/notifications/preferences', '/push/vapid-public', '/push/subscribe']);
});

test('re-enabling reminders reuses the existing browser subscription', async () => {
  let subscribeCalls = 0;
  let savedSubscription;
  let savedPreferences;
  const applicationServerKey = Uint8Array.from([0]).buffer;
  const subscription = {
    endpoint: 'https://push.example.test/device-existing',
    options: {applicationServerKey},
    toJSON() { return {endpoint: this.endpoint, keys: {auth: 'a', p256dh: 'p'}}; },
  };
  const registration = {pushManager: {
    async getSubscription() { return subscription; },
    async subscribe() { subscribeCalls += 1; throw new Error('already subscribed'); },
  }};
  const navigator = {
    userAgent: 'Desktop browser', platform: 'Win32', maxTouchPoints: 0,
    serviceWorker: {ready: Promise.resolve(registration)},
  };
  const window = {PushManager: function PushManager() {}, Notification: {permission: 'granted'}};
  const fetch = async (url, options) => {
    if (url === '/api/notifications/preferences' && !options.method) {
      return {ok: true, async json() { return {channels: []}; }};
    }
    if (url === '/push/vapid-public') return {ok: true, async json() { return {key: 'AA'}; }};
    if (url === '/push/subscribe') {
      savedSubscription = JSON.parse(options.body).subscription;
      return {ok: true};
    }
    if (url === '/api/notifications/preferences' && options.method === 'POST') {
      savedPreferences = JSON.parse(options.body);
      return {ok: true};
    }
    throw new Error(`Unexpected request ${url}`);
  };
  vm.runInNewContext(reminderScript, {window, navigator, Notification: window.Notification,
    fetch, atob, setTimeout, console, Uint8Array, Promise});

  const result = await window.IPReminders.enable({push: true, email: false});

  assert.equal(result.push, true);
  assert.equal(result.saved, true);
  assert.equal(subscribeCalls, 0);
  assert.equal(savedSubscription.endpoint, subscription.endpoint);
  assert.deepEqual(savedPreferences.channels, ['push']);
});

test('a stale VAPID subscription is replaced with the current server key', async () => {
  const order = [];
  let savedSubscription;
  let savedPreferences;
  const oldSubscription = {
    endpoint: 'https://push.example.test/device-old',
    options: {applicationServerKey: Uint8Array.from([9]).buffer},
    async unsubscribe() { order.push('unsubscribe'); return true; },
  };
  const currentSubscription = {
    endpoint: 'https://push.example.test/device-current',
    options: {applicationServerKey: Uint8Array.from([0]).buffer},
    toJSON() { return {endpoint: this.endpoint, keys: {auth: 'a', p256dh: 'p'}}; },
  };
  const registration = {pushManager: {
    async getSubscription() { return oldSubscription; },
    async subscribe(options) {
      order.push('subscribe');
      assert.equal(options.userVisibleOnly, true);
      assert.deepEqual(Array.from(options.applicationServerKey), [0]);
      return currentSubscription;
    },
  }};
  const navigator = {
    userAgent: 'Desktop browser', platform: 'Win32', maxTouchPoints: 0,
    serviceWorker: {ready: Promise.resolve(registration)},
  };
  const window = {PushManager: function PushManager() {}, Notification: {permission: 'granted'}};
  const fetch = async (url, options) => {
    if (url === '/api/notifications/preferences' && !options.method) {
      return {ok: true, async json() { return {channels: []}; }};
    }
    if (url === '/push/vapid-public') return {ok: true, async json() { return {key: 'AA'}; }};
    if (url === '/push/subscribe') {
      savedSubscription = JSON.parse(options.body).subscription;
      return {ok: true};
    }
    if (url === '/api/notifications/preferences' && options.method === 'POST') {
      savedPreferences = JSON.parse(options.body);
      return {ok: true};
    }
    throw new Error(`Unexpected request ${url}`);
  };
  vm.runInNewContext(reminderScript, {window, navigator, Notification: window.Notification,
    fetch, atob, setTimeout, console, Uint8Array, Promise});

  const result = await window.IPReminders.enable({push: true, email: false});

  assert.equal(result.push, true);
  assert.equal(result.saved, true);
  assert.deepEqual(order, ['unsubscribe', 'subscribe']);
  assert.equal(savedSubscription.endpoint, currentSubscription.endpoint);
  assert.deepEqual(savedPreferences.channels, ['push']);
});
