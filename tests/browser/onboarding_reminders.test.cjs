const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/onboarding.html'), 'utf8');
const reminderCode = source.slice(
  source.indexOf('async function onbSaveReminders()'),
  source.indexOf('// ── Custom path', source.indexOf('async function onbSaveReminders()')),
);

function harness({ push = true, permission = 'default' } = {}) {
  const calls = [];
  const elements = {
    onbRemindEmail: { checked: false },
    onbRemindPush: { checked: push },
    onbRemindMsg: { dataset: {}, textContent: '', removeAttribute() {} },
    onbRemindBtn: { disabled: false, textContent: 'Enable' },
    onbRemindSkip: { style: {} },
  };
  const Notification = {
    permission,
    requestPermission() {
      calls.push('permission');
      return Promise.resolve('granted');
    },
  };
  const IPReminders = {
    pushSupported() { return true; },
    enable(wanted, options) {
      calls.push('enable');
      assert.equal(wanted.push, push);
      assert.equal(options.permissionRequest instanceof Promise, permission !== 'granted' && push);
      return Promise.resolve({ saved: true, push: true });
    },
  };
  const context = vm.createContext({
    document: { getElementById: (id) => elements[id] },
    IPReminders,
    Notification,
    onbShow(step) { calls.push(`show:${step}`); },
  });
  vm.runInContext(reminderCode + '\nglobalThis.save = onbSaveReminders;', context);
  return { calls, context };
}

test('onboarding requests browser permission synchronously before enabling reminders', async () => {
  const h = harness();
  const pending = h.context.save();
  assert.deepEqual(h.calls.slice(0, 2), ['permission', 'enable']);
  await pending;
  assert.equal(h.calls.at(-1), 'show:plan');
});

test('already-granted permission does not trigger a redundant browser prompt', async () => {
  const h = harness({ permission: 'granted' });
  await h.context.save();
  assert.deepEqual(h.calls, ['enable', 'show:plan']);
});
