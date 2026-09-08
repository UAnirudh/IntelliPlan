/* Runs static/js/session-chat.js against a minimal DOM so its behaviour can
 * be asserted rather than grepped for. Driven by tests/test_session_chat.py.
 *
 * Deliberately tiny: enough document/element surface for the panel to mount,
 * poll, and render, and no more. A real jsdom dependency for one file is not
 * worth the install in CI.
 *
 * One fidelity detail matters: assigning innerHTML replaces a host's
 * children with fresh elements. The stub does the same, because reusing one
 * button object across two mounts would leave both mounts' click handlers on
 * it and make the double-send guard look broken when it is not.
 *
 * Prints one JSON object of observations on stdout.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const calls = [];      // every fetch the panel made
const warnings = [];   // everything routed through IP.alert / IP.toast
const blockingAlerts = [];  // anything that reached window.alert

const CHILD_IDS = ['sessChatLog', 'sessChatInput', 'sessChatStatus', 'sessChatSend'];

function makeEl(id) {
  const el = {
    id,
    value: '',
    textContent: '',
    disabled: false,
    dataset: {},
    scrollHeight: 1000,
    clientHeight: 300,
    listeners: {},
    children: {},
    renders: 0,        // times innerHTML was assigned
    scrollWrites: 0,   // times scrollTop was written
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
    },
    fire(type, ev) { (this.listeners[type] || []).slice().forEach((f) => f(ev || {})); },
    querySelector(sel) { return this.children[sel.replace('#', '')] || null; },
    querySelectorAll(sel) {
      // Only the save buttons are queried; hand back one stub so the click
      // handler is really bound and a failed save can be observed.
      if (sel && sel.includes('data-save')) {
        if (!el._saveBtn) { el._saveBtn = makeEl('saveBtn'); el._saveBtn.dataset.save = '1'; }
        el._saveBtn.listeners = {};
        return [el._saveBtn];
      }
      return [];
    },
  };
  let html = '';
  Object.defineProperty(el, 'innerHTML', {
    get() { return html; },
    set(v) {
      html = v;
      el.renders += 1;
      // A real innerHTML assignment discards the old subtree. Mint fresh
      // children so handlers bound to the previous ones cannot fire.
      if (el.renders === 1 || CHILD_IDS.some((c) => v.includes(c))) {
        CHILD_IDS.forEach((cid) => { if (v.includes(cid)) el.children[cid] = makeEl(cid); });
      }
    },
  });
  let scroll = 0;
  Object.defineProperty(el, 'scrollTop', {
    get() { return scroll; },
    set(v) { scroll = v; el.scrollWrites += 1; },
  });
  return el;
}

const host1 = makeEl('groupChatHost');

const document = {
  hidden: false,
  _byId: { groupChatHost: host1 },
  _attached: new Set([host1]),
  listeners: {},
  /** Real getElementById searches the whole tree, so the stub does too --
   *  the pre-fix code looks its children up this way rather than scoping to
   *  the host, and the harness has to serve both shapes. */
  getElementById(id) {
    if (this._byId[id]) return this._byId[id];
    for (const el of this._attached) {
      if (el.children && el.children[id]) return el.children[id];
    }
    return null;
  },
  contains(el) { return this._attached.has(el); },
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
  },
  visibilityListenerCount() { return (this.listeners.visibilitychange || []).length; },
  /** Drive a load without waiting out the 8s poll. */
  pokeVisible() { (this.listeners.visibilitychange || []).forEach((f) => f({})); },
};

let messages = [
  { id: 1, author_name: 'Ada', body: 'first', created_at: null, saved_to_library: false },
];

const fetchImpl = (url, options) => {
  calls.push({ url, method: (options && options.method) || 'GET' });
  if (url.includes('/save')) {
    return Promise.resolve({ json: () => Promise.resolve({ status: 'error', message: 'nope' }) });
  }
  if (options && options.method === 'POST') {
    return Promise.resolve({ json: () => Promise.resolve({ status: 'ok' }) });
  }
  return Promise.resolve({ json: () => Promise.resolve({ status: 'ok', messages }) });
};

const liveIntervals = new Set();
const pollBodies = new Map();   // timer id -> the function the panel scheduled
const realSetInterval = setInterval;
const realClearInterval = clearInterval;

const sandbox = {
  document,
  window: { IP: { alert: (m) => warnings.push(m), toast: (m) => warnings.push(m) } },
  alert: (m) => blockingAlerts.push(m),
  fetch: fetchImpl,
  setInterval: (fn, ms) => {
    const t = realSetInterval(fn, ms);
    liveIntervals.add(t);
    pollBodies.set(t, fn);
    return t;
  },
  clearInterval: (t) => { liveIntervals.delete(t); pollBodies.delete(t); realClearInterval(t); },
  parseInt,
  Date,
  console,
};
sandbox.window.document = document;

const ctx = vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'session-chat.js'), 'utf8'),
  ctx);

// `window.initSessionChat` since the IIFE rewrite; the bare global is the
// pre-fix shape, kept so this harness can be pointed at either and show the
// difference rather than just asserting the current one is fine.
const initSessionChat = sandbox.window.initSessionChat || sandbox.initSessionChat;
const settle = () => new Promise((r) => setTimeout(r, 15));

(async () => {
  const out = {};

  initSessionChat({ hostId: 'groupChatHost', contextType: 'group', contextId: 7, canSave: true });
  await settle();
  const log1 = host1.children.sessChatLog;

  // Drive a poll by invoking the body the panel scheduled. Version-neutral:
  // the pre-fix code has no visibilitychange listener, so poking that instead
  // would drive nothing there and every render probe would read as "no
  // redraw" -- flattering the very version being compared against.
  const firePoll = () => pollBodies.forEach((fn) => fn());

  // ── A poll returning what is already shown ────────────────────────
  log1.scrollTop = 0;                       // student has scrolled up to read
  const rendersBefore = log1.renders;
  const scrollBefore = log1.scrollWrites;
  firePoll();
  await settle();
  out.redrew_on_unchanged_poll = log1.renders > rendersBefore;
  out.moved_scroll_on_unchanged_poll = log1.scrollWrites > scrollBefore;

  // ── A new message: redraw, but do not yank a reader to the bottom ──
  messages = messages.concat(
    { id: 2, author_name: 'Grace', body: 'second', created_at: null, saved_to_library: false });
  const rendersBeforeNew = log1.renders;
  const scrollBeforeNew = log1.scrollWrites;
  firePoll();
  await settle();
  out.redrew_on_new_message = log1.renders > rendersBeforeNew;
  out.yanked_scrolled_up_reader = log1.scrollWrites > scrollBeforeNew;

  // ── A reader already at the bottom does follow along ───────────────
  log1.scrollTop = log1.scrollHeight;       // parked at the newest message
  messages = messages.concat(
    { id: 3, author_name: 'Ada', body: 'third', created_at: null, saved_to_library: false });
  const scrollBeforeFollow = log1.scrollWrites;
  firePoll();
  await settle();
  out.followed_reader_at_bottom = log1.scrollWrites > scrollBeforeFollow;

  // ── Polling pauses while the tab is hidden ─────────────────────────
  const getsBeforeHidden = calls.filter((c) => c.method === 'GET').length;
  document.hidden = true;
  firePoll();
  await settle();
  out.polled_while_hidden =
    calls.filter((c) => c.method === 'GET').length > getsBeforeHidden;

  document.hidden = false;
  const getsBeforeVisible = calls.filter((c) => c.method === 'GET').length;
  firePoll();
  await settle();
  out.polled_while_visible =
    calls.filter((c) => c.method === 'GET').length > getsBeforeVisible;

  // ── Re-mounting (groups.html reopening a group) replaces the poll ──
  const intervalsAfterFirstMount = liveIntervals.size;
  const visAfterFirstMount = document.visibilityListenerCount();
  const host2 = makeEl('groupChatHost');
  document._byId.groupChatHost = host2;
  document._attached.delete(host1);         // old panel detached by the rebuild
  document._attached.add(host2);
  initSessionChat({ hostId: 'groupChatHost', contextType: 'group', contextId: 9, canSave: true });
  await settle();
  out.intervals_after_first_mount = intervalsAfterFirstMount;
  out.intervals_after_remount = liveIntervals.size;
  out.vis_listeners_after_first_mount = visAfterFirstMount;
  out.vis_listeners_after_remount = document.visibilityListenerCount();

  // ── Double-send guard, on the live mount ──────────────────────────
  const input2 = host2.children.sessChatInput;
  const send2 = host2.children.sessChatSend;
  input2.value = 'hello room';
  const postsBefore = calls.filter((c) => c.method === 'POST').length;
  send2.fire('click');
  send2.fire('click');                      // impatient second Enter/click
  await settle();
  out.posts_from_double_click = calls.filter((c) => c.method === 'POST').length - postsBefore;

  // ── A failed save reports through IP, never a blocking window.alert ──
  const log2 = host2.children.sessChatLog;
  const saveBtn = log2._saveBtn;
  if (saveBtn) { saveBtn.fire('click'); await settle(); }
  out.exercised_a_failing_save = !!saveBtn;
  out.used_blocking_alert = blockingAlerts.length > 0;
  out.reported_through_ip = warnings.length > 0;

  liveIntervals.forEach((t) => realClearInterval(t));
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})();
