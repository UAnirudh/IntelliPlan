const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/js/test-prep.js'), 'utf8');
const item = { id: 'prep-0123456789ab', title: 'Geometry <b>test</b>', course: 'Geometry',
  date: '2026-11-27', days_away: 3, minutes: 20, suggest_today: true };
const late = { ...item, id: 'prep-111111111111', title: 'English quiz tomorrow', days_away: 1,
  suggest_today: false };

function harness(mode, search = '', fail = false) {
  function element(name) {
    return { name, hidden: true, open: false, value: '', textContent: '', children: [], attrs: {},
      listeners: {}, addEventListener(key, fn) { this.listeners[key] = fn; },
      append(...children) { this.children.push(...children); }, focus() { this.focused = true; },
      dispatchEvent(event) { this.dispatched = event.type; },
      set innerHTML(value) { throw Error('Test prep should render assessment titles as text, not HTML'); } };
  }
  const ids = Object.fromEntries((mode === 'planner' ? ['commandTestPrep', 'commandTestPrepItems']
    : ['tutorTestPrep', 'tutorTestPrepForm', 'tutorTestPrepItems', 'tutorTestPrepStatus',
      'tutorTestPrepTitle', 'tutorTestPrepCourse', 'tutorTestPrepFormat', 'tutorInput'])
    .map(id => [id, element(id)]));
  const requests = [];
  const document = { createElement: element, getElementById: id => ids[id] || null,
    addEventListener(_, fn) { fn(); } };
  vm.runInContext(source, vm.createContext({ document, URLSearchParams, Event,
    window: { location: { search } }, encodeURIComponent,
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (fail) throw Error('Calendar unreachable');
      return { ok: true, json: async () => ({ assessments: [item, late] }) };
    } }));
  return { ...ids, requests };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('Planner shows only 2–3-day reminders and links with an opaque id, not calendar text', async () => {
  const h = harness('planner');
  await flush();
  assert.equal(h.commandTestPrep.hidden, false);
  const rows = h.commandTestPrepItems.children;
  assert.equal(rows.length, 1);
  const [link, summary] = rows[0].children;
  assert.equal(link.href, '/study-and-learn?test-prep=prep-0123456789ab');
  assert.equal(link.textContent, 'Prepare for Geometry <b>test</b>');
  assert.match(summary.textContent, /Test in 3 days · 20 minutes/);
  assert.equal(h.requests[0].options.cache, 'no-store');
});

test('Tutor loads the selected assessment, preserves an existing draft, and stages grading guidance without an AI request', async () => {
  const h = harness('tutor', '?test-prep=prep-0123456789ab');
  await flush();
  assert.equal(h.tutorTestPrep.open, true);
  assert.equal(h.tutorTestPrepTitle.value, item.title);
  assert.equal(h.tutorTestPrepCourse.value, 'Geometry');
  h.tutorTestPrepFormat.value = 'Two-column proofs; grade out of 4.3.';
  h.tutorInput.value = 'Existing thought';
  const submit = () => h.tutorTestPrepForm.listeners.submit({ preventDefault() {} });
  submit();
  assert.equal(h.tutorInput.value, 'Existing thought');
  h.tutorInput.value = '';
  submit();
  assert.match(h.tutorInput.value, /20-minute test prep/);
  assert.match(h.tutorInput.value, /Two-column proofs; grade out of 4\.3/);
  assert.match(h.tutorInput.value, /past mistakes/);
  assert.match(h.tutorInput.value, /grade it against the rubric/);
  assert.match(h.tutorInput.value, /focused retry/);
  assert.equal(h.tutorTestPrep.open, false);
  assert.equal(h.tutorInput.dispatched, 'input');
  assert.deepEqual(h.requests.map(request => request.url), ['/api/tutor/test-prep']);
});

test('Manual Tutor prep is available with no calendar and sends nothing on draft', async () => {
  const h = harness('tutor', '', true);
  assert.equal(h.requests.length, 0);
  h.tutorTestPrep.open = true;
  h.tutorTestPrep.listeners.toggle();
  await flush();
  h.tutorTestPrepTitle.value = 'My practice topic';
  h.tutorTestPrepForm.listeners.submit({ preventDefault() {} });
  assert.match(h.tutorInput.value, /My practice topic/);
  assert.match(h.tutorInput.value, /ask a short question instead of inventing/);
  assert.equal(h.requests.length, 1);
});
