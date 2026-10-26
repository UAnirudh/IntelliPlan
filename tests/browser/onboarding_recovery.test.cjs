const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/onboarding.html'), 'utf8');
const chat = source.slice(source.indexOf('async function onbUseQuickSetup()'), source.indexOf('// ── Import step'));
function harness(status, messages = []) {
  const calls = [], redirects = [];
  const elements = { onbChatMeta: {}, onbInput: { value: 'I study chemistry' }, onbSendBtn: {} };
  const context = vm.createContext({
    ONB: { mode: 'custom', messages },
    document: { getElementById: id => elements[id] },
    onbRenderChat() {},
    window: { location: { assign: url => redirects.push(url) } },
    fetch: async (url, init) => {
      calls.push({ url, body: JSON.parse(init.body) });
      return { status, ok: status === 200, json: async () => ({
        error: status === 503 ? 'safety_unavailable' : undefined,
        reply: status === 503 ? 'Safety check unavailable' : 'What grade are you in?'
      }) };
    }
  });
  vm.runInContext(chat + '\nglobalThis.api = {onbBootstrapChat,onbSendChat};', context);
  return { context, calls, redirects, elements };
}
test('first-turn safety outage opens Quick instead of adding an error reply', async () => {
  const h = harness(503);
  await h.context.api.onbBootstrapChat();
  assert.equal(h.context.ONB.messages.length, 0);
  assert.equal(h.calls[1].body.step, 'quick');
  assert.equal(h.calls[1].body.mode, 'quick');
  assert.deepEqual(h.redirects, ['/onboarding?quick=unavailable']);
});
test('mid-conversation outage keeps answers and saves Quick as the resume step', async () => {
  const h = harness(503, [{role:'assistant', content:'What subjects?'}]);
  await h.context.api.onbSendChat();
  assert.equal(h.calls.length, 2); // No later custom-state write races the recovery.
  assert.equal(h.calls[1].body.messages[1].content, 'I study chemistry');
  assert.equal(h.calls[1].body.step, 'quick');
  assert.equal(h.context.ONB.messages.length, 2);
  assert.equal(h.redirects.length, 1);
});
test('successful chat remains in the customized flow', async () => {
  const h = harness(200);
  await h.context.api.onbBootstrapChat();
  assert.equal(h.context.ONB.messages[0].content, 'What grade are you in?');
  assert.equal(h.calls.length, 1);
  assert.equal(h.redirects.length, 0);
});
test('refusals do not switch to Quick', async () => {
  const h = harness(400);
  await h.context.api.onbSendChat();
  assert.equal(h.redirects.length, 0);
  assert.equal(h.calls.length, 1);
});
