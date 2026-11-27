const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/command_center.js'), 'utf8');
const coverage = fs.readFileSync(path.join(__dirname, '../../static/js/plan-coverage.js'), 'utf8');
const STORE = 'ip_cc_thread_v2';
function harness(owner, initial = {}, savedPlan) {
  const storage = new Map(Object.entries(initial)), requests = [], timers = [];
  const location = { reload() { reloads++; } };
  let boot, reloads = 0;
  function element() {
    return { children: [], style: { setProperty() {} }, value: '', events: {}, parentNode: null,
      addEventListener(name, handler) { this.events[name] = handler; },
      appendChild(child) { child.parentNode = this; this.children.push(child); },
      setAttribute() {}, focus() {}, dispatchEvent(event) { this.events[event.type]?.(event); }, remove() {
        if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this);
      } };
  }
  const ids = Object.fromEntries(['chatThread', 'chatInner', 'chatInput', 'sendMessage', 'chatComposer', 'newConversation'].map(id => [id, element()]));
  if (savedPlan) ids.commandSavedCoverage = element();
  const host = { getAttribute: name => name === 'data-chat-owner' ? owner : 'Plani' };
  const context = vm.createContext({
    document: { getElementById: id => ids[id] || null,
      createElement: element, querySelector: selector => selector.startsWith('[data-') ? host : null,
      documentElement: element(), addEventListener: (_, handler) => { boot = handler; } },
    window: { matchMedia: () => ({ matches: false }), addEventListener() {} },
    sessionStorage: { getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    location, Event: class { constructor(type) { this.type = type; } },
    setTimeout: callback => timers.push(callback),
    fetch: (url, options) => url === '/calendar/connections'
      ? Promise.resolve({ json: async () => ({}) })
      : url === '/schedule/saved' ? Promise.resolve({ ok: true, json: async () => savedPlan })
      : new Promise((resolve, reject) => requests.push({ options, resolve, reject })),
  });
  vm.runInContext(coverage, context);
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
test('Shows the earliest unmet deadline and combines split work in a zero-session preview', async () => {
  const h = harness('17');
  h.submit('Plan my week');
  h.requests[0].resolve({ status: 409, json: async () => ({
    status: 'error', reply: 'No sessions fit.', retryable: false,
    schedule: { preview: true, schedule: [], deferred: [
      { task_id: 'essay', title: 'Essay', minutes: 40, due_date: '2026-11-26' },
      { task_id: 'math', title: '<Algebra>', minutes: 15, deadline_at: '2026-11-25T08:30:00' },
      { task_id: 'math', title: '<Algebra>', minutes: 20, due_date: '2026-11-25' }
    ] },
  }) });
  await flush();
  const card = h.ids.chatInner.children.at(-1).innerHTML;
  assert.match(card, /Preview · not saved/);
  assert.match(card, /1h 15m still needs time/);
  assert.match(card, /35m to schedule · due 2026-11-25 at 8:30 AM/);
  assert.equal(card.match(/&lt;Algebra&gt;/g).length, 1);
  assert.ok(card.indexOf('&lt;Algebra&gt;') < card.indexOf('Essay'));
  assert.doesNotMatch(card, /Open in Scheduler/);
});
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
test('provider outage restores draft and excludes failed turn from later requests', async () => {
  const h = harness('17'); h.submit('Make a study plan');
  h.requests[0].resolve({ status: 503, json: async () => ({
    status: 'error', error: 'ai_unavailable', reply: 'Plani is temporarily unavailable.',
  }) });
  await flush();

  assert.equal(h.ids.chatInput.value, 'Make a study plan');
  assert.deepEqual(JSON.parse(h.storage.get(STORE)).history, []);
  assert.equal(JSON.parse(h.storage.get(STORE)).log.some(item => item.role === 'user'), false);
  assert.equal(h.ids.chatInner.children.some(el => /temporarily unavailable/.test(el.innerHTML)), true);

  h.submit('Try again');
  assert.deepEqual(JSON.parse(h.requests[1].options.body).messages,
    [{ role: 'user', content: 'Try again' }]);
});
test('provider failure after a completed tool action closes the turn in history', async () => {
  const h = harness('17'); h.submit('Create a review task');
  h.requests[0].resolve({ status: 503, json: async () => ({
    status: 'error', error: 'ai_unavailable', reply: 'The final reply could not be generated.',
    actions: ['Created review task'],
  }) });
  await flush();

  assert.equal(h.ids.chatInput.value, '');
  assert.deepEqual(JSON.parse(h.storage.get(STORE)).history, [
    { role: 'user', content: 'Create a review task' },
    { role: 'assistant', content: 'The final reply could not be generated.' },
  ]);
  h.submit('What happened?');
  assert.deepEqual(JSON.parse(h.requests[1].options.body).messages, [
    { role: 'user', content: 'Create a review task' },
    { role: 'assistant', content: 'The final reply could not be generated.' },
    { role: 'user', content: 'What happened?' },
  ]);
});
test('transport failure restores the draft and removes the unprocessed turn', async () => {
  const h = harness('17'); h.submit('Help me plan');
  h.requests[0].reject(new Error('offline'));
  await flush();

  assert.equal(h.ids.chatInput.value, 'Help me plan');
  assert.deepEqual(JSON.parse(h.storage.get(STORE)).history, []);
  assert.match(h.ids.chatInner.children.at(-1).innerHTML, /Check your connection/);
});
test('Navigation offers a link and waits for the user to open it', async () => {
  const h = harness('17'); h.submit();
  h.requests[0].resolve({ status: 200, json: async () => ({ status: 'ok', reply: 'Open plan', navigate: '/scheduler' }) });
  await flush();
  assert.equal(h.timers.length, 0);
  assert.equal(h.ids.chatInner.children.at(-1).children[0].href, '/scheduler');
  assert.equal(h.location.href, undefined);
});
test('Old and new memory badges stay hidden without hiding the saved plan', async () => {
  const badge = { t: 'action', text: '✓ Saved to memories: Study Schedule' };
  const h = harness('17', { [STORE]: JSON.stringify({ owner: '17', history: [], log: [badge] }) });
  assert.equal(h.ids.chatInner.children.length, 0);
  h.submit('Plan my week');
  h.requests[0].resolve({ status: 200, json: async () => ({
    status: 'ok', reply: 'Your study plan is ready.', actions: [badge.text],
    schedule: { schedule: [{ day_name: 'Monday', blocks: [{ assignment: 'Reading' }] }] },
  }) });
  await flush();
  assert.match(h.ids.chatInner.children.at(-1).innerHTML, /Reading/);
  assert.equal(JSON.parse(h.storage.get(STORE)).log.some(item => item.t === 'action'), false);
});

test('The opening view flags upcoming work from a saved plan without making a chat request', async () => {
  const date = new Date();
  date.setDate(date.getDate() + 2);
  const due = [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0')].join('-');
  const h = harness('17', {}, { status: 'ok', data: { deferred: [
    { title: '<Lab>', minutes: 20, due_date: due },
    { title: 'Past homework', minutes: 30, due_date: '2001-01-01' },
    { title: 'Future homework', minutes: 30, due_date: '2999-01-01' },
  ] } });
  await flush();
  assert.match(h.ids.commandSavedCoverage.innerHTML, /From your saved plan/);
  assert.match(h.ids.commandSavedCoverage.innerHTML, /20m still needs time/);
  assert.match(h.ids.commandSavedCoverage.innerHTML, /&lt;Lab&gt;/);
  assert.doesNotMatch(h.ids.commandSavedCoverage.innerHTML, /Past homework|Future homework/);
  assert.equal(h.requests.length, 0);
});

test('read-only priority shortcut runs directly and preserves an unsent message', () => {
  const h = harness('17');
  const priority = {dataset: {prompt: 'Look at my upcoming assignments and help me choose what to do first.'},
    hasAttribute: name => name === 'data-run-prompt'};
  const click = button => h.ids.chatInner.events.click({target: {closest: () => button}});
  h.ids.chatInput.value = 'My unfinished question';
  click(priority);
  assert.equal(h.requests.length, 0);
  assert.equal(h.ids.chatInput.value, 'My unfinished question');

  h.ids.chatInput.value = '';
  const plan = {dataset: {prompt: 'Plan my week using my assignments and connected calendar.'},
    hasAttribute: () => false};
  click(plan);
  assert.equal(h.requests.length, 0);
  assert.equal(h.ids.chatInput.value, plan.dataset.prompt);

  h.ids.chatInput.value = '';
  click(priority);
  assert.equal(h.requests.length, 1);
  assert.equal(JSON.parse(h.requests[0].options.body).messages.at(-1).content, priority.dataset.prompt);
  assert.equal(h.ids.chatInput.value, '');
});
