'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const template = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/scheduler.html'), 'utf8');
const start = template.indexOf('async function loadSchedulerAssignments()');
const end = template.indexOf('\ndocument.getElementById(', start);
assert.ok(start > 0 && end > start);
const loader = template.slice(start, end);

test('schedule generation sends the browser timezone for local-day planning', () => {
  assert.match(template, /timezone: Intl\.DateTimeFormat\(\)\.resolvedOptions\(\)\.timeZone/);
});

test('interactive summary never calls the first upcoming day today', () => {
  assert.match(template, /const todayDay = sched\.find\(d => \(d\.date \|\| ''\)\.slice\(0, 10\) === todayIso\);/);
  assert.match(template, /Next planned day \(\$\{schedFmtDate\(nextDay\.date\)\}/);
  assert.doesNotMatch(template, /todayDay = sched\.find\([^\n]+\) \|\| sched\[0\]/);
});

test('daily study capacity shows spare time and overload without inventing availability', () => {
  const start = template.indexOf('function schedFmtMinutes(');
  const end = template.indexOf('\n}', template.indexOf('function schedStudyCapacity(')) + 2;
  assert.ok(start > 0 && end > start);
  const context = vm.createContext({});
  vm.runInContext(template.slice(start, end), context);
  const today = '2026-11-25';
  const day = { date: today, study_minutes: 30, study_capacity_minutes: 90, total_minutes: 40 };
  const capacity = context.schedStudyCapacity;
  assert.equal(capacity(day, today), '1h unallocated study time');
  assert.equal(capacity({ ...day, study_minutes: 90 }, today), 'Study capacity filled');
  assert.equal(capacity({ ...day, study_minutes: 105 }, today), '15 min over study capacity');
  assert.equal(capacity({ ...day, study_capacity_minutes: 0 }, today), '30 min over study capacity');
  assert.equal(capacity({ ...day, study_capacity_minutes: null }, today), '');
  assert.equal(capacity({ ...day, date: '2026-11-24' }, today), '');
});

test('checklist drawer exposes escaped per-session planner reasons and stage labels', () => {
  assert.match(template, /id="ckReasonPanel" aria-labelledby="ckReasonTitle" hidden/);
  assert.match(template, /if \(block\.stage_title\) meta\.push\(.*Stage:/);
  const start = template.indexOf('function renderBlockReasons(');
  const end = template.indexOf('\n}', start) + 2;
  assert.ok(start > 0 && end > start, 'reason renderer should stay a standalone function');
  const document = {
    createElement(tag) { return { tag, textContent: '' }; },
  };
  const context = { document };
  vm.createContext(context);
  vm.runInContext(template.slice(start, end), context);
  const list = {
    children: [],
    set textContent(value) { this.children = []; this.value = value; },
    appendChild(item) { this.children.push(item); },
  };
  const panel = { hidden: true };
  context.renderBlockReasons({
    reasons: ['Due tomorrow.', '<img src=x onerror=alert(1)>'],
  }, panel, list);
  assert.equal(panel.hidden, false);
  assert.deepEqual(list.children.map(item => item.textContent), [
    'Due tomorrow.', '<img src=x onerror=alert(1)>',
  ]);
  assert.ok(list.children.every(item => item.tag === 'li'));

  context.renderBlockReasons({ reasons: [] }, panel, list);
  assert.equal(panel.hidden, true);
  assert.equal(list.children.length, 0);
});

function fixture() {
  const pending = [];
  const ctx = {
    _assignmentLoadGeneration: 0, _assignmentQueueRows: [], allAssignments: [], renders: 0,
    renderAssignmentQueue() { ctx.renders++; },
    fetch(url, options) {
      assert.equal(url, '/api/scheduler/assignments?include_waiting=1');
      assert.equal(options.cache, 'no-store');
      return new Promise((resolve, reject) => pending.push({
        reply(rows) { resolve({ok: true, json: async () => rows}); }, reject,
      }));
    },
  };
  vm.createContext(ctx);
  vm.runInContext(loader, ctx);
  return {ctx, pending, load: () => ctx.loadSchedulerAssignments()};
}

test('an older assignment snapshot cannot restore newly submitted work', async () => {
  const f = fixture();
  const older = f.load(), newer = f.load();
  f.pending[1].reply([{id: 'B', requires_action: false, awaiting_grade: true}]);
  assert.equal(await newer, true);
  f.pending[0].reply([{id: 'B', requires_action: true}]);
  assert.equal(await older, false);
  assert.equal(f.ctx.allAssignments.length, 0);
  assert.equal(f.ctx._assignmentQueueRows[0].awaiting_grade, true);
  assert.equal(f.ctx.renders, 1);
});

test('an obsolete assignment fetch failure cannot overwrite a newer success', async () => {
  const f = fixture();
  const older = f.load(), newer = f.load();
  f.pending[1].reply([{id: 'redo', requires_action: true}]);
  await newer;
  f.pending[0].reject(new Error('Old request failed'));
  assert.equal(await older, false);
  assert.equal(f.ctx.allAssignments[0].id, 'redo');
  assert.equal(f.ctx.renders, 1);
});

test('a current fetch failure is visible and preserves the last valid assignment list', async () => {
  const f = fixture();
  f.ctx.allAssignments = [{id: 'current', requires_action: true}];
  const request = f.load();
  f.pending[0].reply({error: 'not a list'});
  await assert.rejects(request, /Assignments could not be loaded/);
  assert.equal(f.ctx.allAssignments[0].id, 'current');
  assert.equal(f.ctx.renders, 0);
});

test('weekly review previews only open work due next week and saves only after confirmation', async () => {
  const extract = (name, next) => {
    const start = template.indexOf(name);
    const end = template.indexOf(next, start);
    assert.ok(start > 0 && end > start);
    return template.slice(start, end);
  };
  const calls = [];
  const elements = new Map([['hoursPerDay', {value: '2', style: {}}],
    ['preferredTime', {value: 'evening', style: {}}]]);
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {value: '', style: {}});
      return elements.get(id);
    },
  };
  const plan = {schedule: [{date: '2026-11-30', blocks: [{block_id: 'd1-b1'}]}]};
  let generated = plan;
  let saveSucceeds = false;
  let persisted = { 'd1-b1': {done: true}, 'unrelated': {done: true} };
  let writes = 0;
  let autoSaves = 0;
  const context = vm.createContext({
    document,
    reviewWeek: {start: '2026-11-30', end: '2026-12-06'},
    allAssignments: [
      {title: 'Earlier', due_date: '2026-11-29'},
      {title: 'Essay', due_date: '2026-11-30T20:00:00Z'},
      {title: 'Reading', due_date: '2026-12-06'},
      {title: 'Submitted', due_date: '2026-12-02', requires_action: false},
      {title: 'Later', due_date: '2026-12-07'},
      {title: 'Undated'},
    ],
    customTasks: ['Manual input on another screen'],
    _progressSyncEnabled: false, _lastScheduleData: null, _clarifyAnswers: {},
    scheduleProgressPush() {},
    localStorage: {
      getItem() { return JSON.stringify(persisted); },
      setItem(key, value) { persisted = JSON.parse(value); writes++; },
    },
    async fetch(url, req) {
      calls.push([url, JSON.parse(req.body)]);
      if (url === '/generate_schedule') return {json: async () => ({status: 'ok', data: generated})};
      assert.equal(url, '/schedule/save');
      return {json: async () => ({status: saveSucceeds ? 'ok' : 'error'})};
    },
    hideGenError() {}, hideClarify() {}, showUsedPresets() {}, updateBadge() {},
    showGenError(message) { throw new Error(message); },
    renderSchedule(data) { assert.equal(data, generated); },
    autoSaveSchedule() { autoSaves++; },
  });
  vm.runInContext([
    extract('function reviewWeekAssignments', 'let _assignmentQueueRows'),
    extract('const CK_STATE_KEY', 'let _progressSyncEnabled'),
    extract('function generateSchedule(extra)', 'function updateCalExportButton'),
    extract('async function saveSchedule()', 'async function saveToMemories'),
  ].join('\n'), context);

  generated = {schedule: []};
  await context.generateSchedule();
  assert.equal(elements.get('savePanel').style.display, 'none');
  assert.match(elements.get('reviewDraftStatus').textContent, /No study sessions fit/);
  assert.equal(writes, 0);
  calls.length = 0;

  generated = plan;
  await context.generateSchedule();
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0][1].assignments.map(row => row.title), ['Essay', 'Reading']);
  assert.deepEqual(calls[0][1].custom_tasks, []);
  assert.equal(calls[0][1].skip_clarify, true);
  assert.equal(calls[0][1].review_draft, true);
  assert.equal(writes, 0);
  assert.equal(autoSaves, 0);
  assert.equal(persisted['d1-b1'].done, true);
  assert.match(elements.get('reviewDraftStatus').textContent, /replace your active/);

  await context.saveSchedule();
  assert.equal(writes, 0);
  assert.equal(elements.get('saveScheduleBtn').disabled, false);
  assert.match(elements.get('reviewDraftStatus').textContent, /could not be saved/);
  saveSucceeds = true;
  await Promise.all([context.saveSchedule(), context.saveSchedule()]);
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[2][1].schedule_data, plan);
  assert.equal(writes, 1);
  assert.deepEqual(persisted, {unrelated: {done: true}});
  assert.match(elements.get('reviewDraftStatus').textContent, /Saved as your active/);
});
