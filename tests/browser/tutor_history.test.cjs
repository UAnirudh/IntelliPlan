const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/tutor.html'), 'utf8');
const history = source.slice(source.indexOf('async function loadConversations()'), source.indexOf('function openTutorDrawer()'));
const escaping = source.slice(source.indexOf('function escHtml(t)'), source.indexOf('function renderMd(raw)'));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
const reply = data => ({ ok: true, json: async () => data });

function harness(fetch, confirm = async () => true) {
  const elements = Object.fromEntries(['tutorInput', 'tutorMessages', 'tutorHistoryList', 'tutorHistoryStatus']
    .map(id => [id, { value: '', innerHTML: '', textContent: '', focus() {} }]));
  const rendered = [];
  const context = vm.createContext({ fetch, document: { getElementById: id => elements[id] },
    IP: { confirm }, console, conversationId: 7, conversationList: [{ id: 7, title: 'Saved chat' }],
    tutorHistory: [{ role: 'user', content: 'My saved question' }], isProcessing: false,
    conversationLoadVersion: 0, requestedConversationId: null, deletingConversation: false,
    summarizeConversation() {}, stopSpeaking() {}, clearTutorAssignment() {}, closeTutorDrawer() {},
    showChat() {}, resetToEmpty() {}, scrollBottom() {},
    visibleTutorText: text => text,
    addTutorMsg: (role, text) => rendered.push([role, text]),
  });
  vm.runInContext(escaping + history, context);
  return { api: context, elements, rendered };
}

test('a delayed history response cannot replace a more recently selected chat', async () => {
  const first = deferred(), second = deferred();
  const { api, rendered } = harness(url => url.endsWith('/1') ? first.promise : second.promise);
  const old = api.loadConversation(1), current = api.loadConversation(2);
  second.resolve(reply({ id: 2, messages: [{ role: 'assistant', content: 'Second chat' }] }));
  await current;
  first.resolve(reply({ id: 1, messages: [{ role: 'assistant', content: 'Old chat' }] }));
  await old;
  assert.equal(api.conversationId, 2);
  assert.deepEqual(rendered, [['bot', 'Second chat']]);
});

test('starting a new chat invalidates a pending saved-chat load', async () => {
  const wait = deferred();
  const { api, rendered } = harness(() => wait.promise);
  const pending = api.loadConversation(1);
  await api.startNewChat();
  wait.resolve(reply({ id: 1, messages: [{ role: 'assistant', content: 'Old chat' }] }));
  await pending;
  assert.equal(api.conversationId, null);
  assert.equal(api.tutorHistory.length, 0);
  assert.deepEqual(rendered, []);
});

test('the initial history fetch never opens a saved chat over a typed draft', async () => {
  const requests = [];
  const { api, elements } = harness(async url => {
    requests.push(url);
    return reply({ conversations: [{ id: 9, title: 'Older chat' }] });
  });
  api.tutorHistory = [];
  elements.tutorInput.value = 'A new question I am typing';
  await api.loadConversations();
  assert.deepEqual(requests, ['/api/tutor/conversations']);
  assert.equal(elements.tutorInput.value, 'A new question I am typing');
});

test('a denied delete preserves the chat and reports the failure', async () => {
  const { api, elements } = harness(async () => ({ ok: false, json: async () => ({ error: 'institution_policy' }) }));
  elements.tutorMessages.innerHTML = 'Visible saved chat';
  await api.deleteConversation(7);
  assert.equal(api.conversationId, 7);
  assert.equal(api.conversationList.length, 1);
  assert.equal(elements.tutorMessages.innerHTML, 'Visible saved chat');
  assert.match(elements.tutorHistoryStatus.textContent, /Could not delete/);
  assert.equal(api.deletingConversation, false);
});

test('a network failure while deleting preserves the chat', async () => {
  const { api, elements } = harness(async () => { throw new Error('offline'); });
  await api.deleteConversation(7);
  assert.equal(api.conversationId, 7);
  assert.equal(api.tutorHistory[0].content, 'My saved question');
  assert.match(elements.tutorHistoryStatus.textContent, /still here/);
});

test('only a confirmed successful delete clears the active chat', async () => {
  const { api, elements } = harness(async () => reply({ ok: true }));
  await api.deleteConversation(7);
  assert.equal(api.conversationId, null);
  assert.equal(api.conversationList.length, 0);
  assert.equal(api.tutorHistory.length, 0);
  assert.equal(elements.tutorHistoryStatus.textContent, 'Chat deleted.');
});

test('canceling deletion makes no request', async () => {
  const { api } = harness(() => assert.fail('Unexpected delete'), async () => false);
  await api.deleteConversation(7);
  assert.equal(api.conversationId, 7);
});

test('a successful delete invalidates an already pending response for that chat', async () => {
  const wait = deferred();
  const { api, rendered } = harness((url, options) => options?.method === 'DELETE' ? reply({ ok: true }) : wait.promise);
  const pending = api.loadConversation(7);
  await api.deleteConversation(7);
  wait.resolve(reply({ id: 7, messages: [{ role: 'assistant', content: 'Deleted content' }] }));
  await pending;
  assert.equal(api.conversationId, null);
  assert.deepEqual(rendered, []);
});

test('redirected history loads leave the visible conversation intact', async () => {
  const { api, elements } = harness(async () => ({ ok: true, redirected: true }));
  await api.loadConversation(9);
  assert.equal(api.conversationId, 7);
  assert.match(elements.tutorHistoryStatus.textContent, /Could not open/);
});

test('saved chat titles cannot inject markup or break the delete button label', () => {
  const { api, elements } = harness(() => assert.fail('Unexpected request'));
  api.conversationList = [{ id: 7, title: '<img src=x>" autofocus onfocus="alert(1)' }];
  api.renderHistory();
  const html = elements.tutorHistoryList.innerHTML;
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('onfocus="alert(1)'));
  assert.ok(html.includes('&lt;img src=x&gt;&quot;'));
});

test('invalid conversation identifiers cannot enter inline click handlers', () => {
  const { api, elements } = harness(() => assert.fail('Unexpected request'));
  api.conversationList = [{ id: '7);alert(1)', title: 'Untrusted record' }];
  api.renderHistory();
  assert.ok(!elements.tutorHistoryList.innerHTML.includes('alert'));
  assert.ok(!elements.tutorHistoryList.innerHTML.includes('Untrusted record'));
});
