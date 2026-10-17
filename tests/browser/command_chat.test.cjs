const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/command_center.js'), 'utf8');
const STORE = 'ip_cc_thread_v2';
function harness(owner, initial = {}) {
  const storage = new Map(Object.entries(initial)), requests = [], timers = [];
  const location = { reload() { reloads++; } };
  let boot, reloads = 0;
  function element() {
    return { children: [], style: { setProperty() {} }, value: '', events: {},
      addEventListener(name, handler) { this.events[name] = handler; },
      appendChild(child) { this.children.push(child); },
      setAttribute() {}, focus() {}, remove() {} };
  }
  const ids = Object.fromEntries(['chatThread', 'chatInner', 'chatInput', 'sendMessage', 'chatComposer', 'newConversation'].map(id => [id, element()]));
  const host = { getAttribute: name => name === 'data-chat-owner' ? owner : 'Plani' };
  const context = vm.createContext({
    document: { getElementById: id => ids[id] || null,
      createElement: element, querySelector: selector => selector.startsWith('[data-') ? host : null,
      documentElement: element(), addEventListener: (_, handler) => { boot = handler; } },
    window: { matchMedia: () => ({ matches: false }), addEventListener() {} },
    sessionStorage: { getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    location, setTimeout: callback => timers.push(callback),
    fetch: (url, options) => url === '/calendar/connections'
      ? Promise.resolve({ json: async () => ({}) })
      : new Promise(resolve => requests.push({ options, resolve })),
  });
  vm.runInContext(source, context); boot();
  return { storage, requests, ids, timers, location, reloads: () => reloads,
    submit(text = 'New task') { ids.chatInput.value = text; ids.chatComposer.events.submit({ preventDefault() {} }); },
    reset() { ids.newConversation.events.click(); } };
}
function saved(owner) {
  return JSON.stringify({ owner, history: [{ role: 'user', content: 'Private prior question' }],
    log: [{ t: 'msg', role: 'user', text: 'Private prior question' }] });
}
async function flush() { await new Promise(resolve => setImmediate(resolve)); }
test('matching account restores its conversation and sends the owner contract', () => {
  const h = harness('17', { [STORE]: saved('17') }); h.submit();
  assert.equal(h.ids.chatInner.children[0].innerHTML, 'Private prior question');
  assert.equal(h.requests[0].options.headers['X-IntelliPlan-Chat-Owner'], '17');
  assert.equal(JSON.parse(h.requests[0].options.body).messages[0].content, 'Private prior question');
});
test('another account never renders or sends the cached conversation', () => {
  const h = harness('18', { [STORE]: saved('17') });
  assert.equal(h.ids.chatInner.children.length, 0); assert.equal(h.storage.has(STORE), false);
  h.submit();
  assert.deepEqual(JSON.parse(h.requests[0].options.body).messages, [{ role: 'user', content: 'New task' }]);
  assert.equal(JSON.parse(h.storage.get(STORE)).owner, '18');
});
test('ownerless legacy history is removed instead of migrated', () => {
  const h = harness('18', { ip_cc_thread_v1: saved(undefined) }); h.submit();
  assert.equal(h.storage.has('ip_cc_thread_v1'), false);
  assert.equal(JSON.parse(h.requests[0].options.body).messages.length, 1);
});
test('missing page identity neither restores nor persists a conversation', () => {
  const h = harness('', { [STORE]: saved('17') }); h.submit();
  assert.equal(h.storage.has(STORE), false);
  assert.equal(JSON.parse(h.requests[0].options.body).messages.length, 1);
});
test('late replies cannot resurrect a cleared conversation', async () => {
  const h = harness('17'); h.submit(); h.reset();
  h.requests[0].resolve({ status: 200, json: async () => ({ status: 'ok', reply: 'Stale reply' }) });
  await flush();
  assert.equal(h.storage.has(STORE), false);
  assert.equal(h.ids.chatInner.children.some(el => el.innerHTML === 'Stale reply'), false);
  assert.equal(h.reloads(), 1);
});
test('account change response clears conversation and reloads without applying actions', async () => {
  const h = harness('17'); h.submit();
  h.requests[0].resolve({ status: 409, json: async () => ({ error: 'account_changed', actions: ['Must not render'] }) });
  await flush();
  assert.equal(h.storage.has(STORE), false); assert.equal(h.reloads(), 1);
  assert.equal(h.ids.chatInner.children.some(el => el.textContent === 'Must not render'), false);
});
test('New chat also suppresses navigation queued by the old reply', async () => {
  const h = harness('17'); h.submit();
  h.requests[0].resolve({ status: 200, json: async () => ({ status: 'ok', reply: 'Open plan', navigate: '/scheduler' }) });
  await flush();
  assert.equal(h.timers.length, 1);
  h.reset(); h.timers[0]();
  assert.equal(h.location.href, undefined);
});
