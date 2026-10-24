'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/session-chat.js'), 'utf8');

function fixture() {
  function element() {
    let html = '';
    return {
      children: {}, listeners: {}, value: '', textContent: '', disabled: false, hidden: false,
      renders: 0, scrollTop: 0, scrollHeight: 1000, clientHeight: 300,
      get innerHTML() { return html; },
      set innerHTML(value) {
        html = value; this.renders++;
        for (const match of value.matchAll(/id="([^"]+)"/g)) this.children[match[1]] = element();
      },
      addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); },
      querySelector(name) { return this.children[name.slice(1)]; },
      querySelectorAll() { return []; },
      fire(name) { for (const fn of this.listeners[name] || []) fn({}); },
    };
  }
  const host = element(), calls = [], replies = [], intervals = new Set();
  const document = {
    hidden: false, listeners: {}, getElementById() { return host; }, contains() { return true; },
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); },
    removeEventListener(name, fn) { this.listeners[name] = (this.listeners[name] || []).filter(x => x !== fn); },
  };
  const window = { IP: { alert() {} } };
  vm.runInNewContext(source, {
    window, document, Map, Array, Number, Promise, Date,
    setInterval(fn) { intervals.add(fn); return fn; }, clearInterval(fn) { intervals.delete(fn); },
    fetch(url, options) {
      calls.push({ url, options });
      if (!replies.length) throw new Error('Unexpected request');
      const next = replies.shift();
      return typeof next === 'function' ? next() : Promise.resolve({ ok: next.ok !== false, json: () => Promise.resolve(next) });
    },
  });
  return {
    host, calls, replies, document,
    start() { window.initSessionChat({ hostId: 'host', contextType: 'live', contextId: 7 }); },
    poll() { for (const fn of intervals) fn(); },
    child(id) { return host.children[id]; },
  };
}
const message = id => ({ id, author_name: 'Ada', body: 'Message ' + id, saved_to_library: false });
const page = (ids, extra = {}) => ({ status: 'ok', messages: ids.map(message), next_after_id: ids.at(-1) || 0,
  has_more: false, has_more_before: false, ...extra });
const settle = () => new Promise(resolve => setImmediate(resolve));

test('unchanged polls use after cursor and do not redraw retained history', async () => {
  const f = fixture(); f.replies.push(page([101, 102], { has_more_before: true })); f.start(); await settle();
  const log = f.child('sessChatLog'), renders = log.renders;
  f.replies.push(page([], { next_after_id: 102 })); f.poll(); await settle();
  assert.match(f.calls.at(-1).url, /\?after_id=102$/);
  assert.equal(log.renders, renders);
  assert.match(log.innerHTML, /Message 101/);
  assert.equal(f.child('sessChatOlder').hidden, false);
});

test('older history loads backwards without changing the forward cursor', async () => {
  const f = fixture(); f.replies.push(page([101, 102], { has_more_before: true })); f.start(); await settle();
  f.replies.push(page([99, 100])); f.child('sessChatOlder').fire('click'); await settle();
  assert.match(f.calls.at(-1).url, /\?before_id=101$/);
  assert.match(f.child('sessChatLog').innerHTML, /Message 99/);
  assert.match(f.child('sessChatLog').innerHTML, /Message 102/);
  f.replies.push(page([], { next_after_id: 102 })); f.poll(); await settle();
  assert.match(f.calls.at(-1).url, /\?after_id=102$/);
});

test('failures and malformed cursor pages retry from the accepted cursor', async () => {
  const f = fixture(); f.replies.push(page([5])); f.start(); await settle();
  for (const bad of [{ ok: false, status: 'error' }, page([], { next_after_id: 900 }), page([7, 6])]) {
    f.replies.push(bad); f.poll(); await settle();
  }
  f.replies.push(page([6])); f.poll(); await settle();
  for (const call of f.calls.slice(1)) assert.match(call.url, /\?after_id=5$/);
  assert.match(f.child('sessChatLog').innerHTML, /Message 5/);
  assert.match(f.child('sessChatLog').innerHTML, /Message 6/);
  assert.doesNotMatch(f.child('sessChatLog').innerHTML, /Message 7/);
});

test('offline catch-up follows bounded forward pages without gaps', async () => {
  const f = fixture(); f.replies.push(page([5])); f.start(); await settle();
  f.replies.push(page([6, 7], { has_more: true }), page([8, 9], { has_more: true }), page([10]));
  f.poll(); await settle();
  assert.deepEqual(f.calls.slice(1).map(x => x.url.split('?')[1]), ['after_id=5', 'after_id=7', 'after_id=9']);
  for (const id of [5, 6, 7, 8, 9, 10]) assert.match(f.child('sessChatLog').innerHTML, new RegExp('data-id="' + id + '"'));
});

test('slow requests never overlap and background tabs do not poll', async () => {
  const f = fixture(); let resolve;
  f.replies.push(() => new Promise(r => { resolve = r; })); f.start(); f.poll(); f.poll();
  assert.equal(f.calls.length, 1);
  resolve({ ok: true, json: () => Promise.resolve(page([5])) }); await settle();
  f.document.hidden = true; f.poll(); await settle(); assert.equal(f.calls.length, 1);
  f.document.hidden = false; f.replies.push(page([], { next_after_id: 5 })); f.poll(); await settle();
  assert.equal(f.calls.length, 2);
});
