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
      assert.equal(url, '/live?include_waiting=1');
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
