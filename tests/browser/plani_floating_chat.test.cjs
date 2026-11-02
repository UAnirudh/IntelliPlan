'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/base.html'), 'utf8');
const start = source.indexOf('function addMsg(role, text, record = true) {');
const end = source.indexOf('\nfunction handlePlaniKey', start);
assert.ok(start >= 0 && end > start, 'floating Plani send functions should remain a standalone block');
const chatFunctions = source.slice(start, end);

class Element {
  constructor() {
    this.children = [];
    this.attributes = {};
    this.style = {};
    this.classList = {toggle() {}, add() {}, remove() {}};
    this.value = '';
    this.disabled = false;
    this.textContent = '';
    this.scrollTop = 0;
    this.scrollHeight = 100;
  }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  setAttribute(name, value) { this.attributes[name] = value; }
  remove() {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this);
  }
  focus() { this.focused = true; }
}

function harness(response) {
  const ids = Object.fromEntries(['planiMessages', 'planiTyping', 'planiInput', 'planiSendBtn']
    .map(id => [id, new Element()]));
  ids.planiInput.value = 'Help me study';
  const requests = [];
  const context = vm.createContext({
    document: {getElementById: id => ids[id], createElement: () => new Element()},
    fetch: async (url, options) => { requests.push({url, options}); return response; },
    planiHistory: [],
  });
  vm.runInContext(chatFunctions, context);
  return {context, ids, requests};
}

test('provider outage keeps the prompt in the composer and out of failed chat history', async () => {
  const h = harness({ok: false, json: async () => ({
    error: 'ai_unavailable', retryable: true,
    reply: "Plani's AI is not reachable right now. This is on our side, not yours.",
  })});
  await h.context.sendPlaniMessage();

  assert.equal(h.requests.length, 1);
  assert.deepEqual(JSON.parse(h.requests[0].options.body).messages,
    [{role: 'user', content: 'Help me study'}]);
  assert.equal(h.ids.planiInput.value, 'Help me study');
  assert.deepEqual(Array.from(h.context.planiHistory), []);
  assert.equal(h.ids.planiMessages.children.length, 1);
  assert.match(h.ids.planiMessages.children[0].children[1].textContent, /on our side/);
  assert.match(h.ids.planiMessages.children[0].children[1].textContent, /retry from the composer/);
  assert.equal(h.ids.planiSendBtn.disabled, false);
});

test('successful reply records the user turn once and clears the sent prompt', async () => {
  const h = harness({ok: true, json: async () => ({reply: 'Start with one small step.'})});
  await h.context.sendPlaniMessage();

  assert.equal(h.ids.planiInput.value, '');
  assert.deepEqual(Array.from(h.context.planiHistory, row => [row.role, row.content]), [
    ['user', 'Help me study'], ['assistant', 'Start with one small step.'],
  ]);
  assert.equal(h.ids.planiMessages.children.length, 2);
});
