import assert from "node:assert/strict";
import fs from "node:fs";
import Module, { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "..");
const remindersPath = path.join(root, "lib", "reminders.ts");
const remindersSource = fs.readFileSync(remindersPath, "utf8");

let enabled = "1";
let tasks = [];
let taskFetches = 0;
let scheduled = [];
let nextId = 0;
let beforeResolveTasks;
let fetchTasksMock;

const notificationMock = {
  SchedulableTriggerInputTypes: { DATE: "date" },
  getPermissionsAsync: async () => ({ granted: true }),
  requestPermissionsAsync: async () => ({ granted: true }),
  getAllScheduledNotificationsAsync: async () => scheduled,
  cancelScheduledNotificationAsync: async (identifier) => {
    scheduled = scheduled.filter((item) => item.identifier !== identifier);
  },
  scheduleNotificationAsync: async (input) => {
    const identifier = `notice-${++nextId}`;
    scheduled.push({ identifier, content: input.content, trigger: input.trigger });
    return identifier;
  },
};

const loaded = new Module(remindersPath);
loaded.filename = remindersPath;
loaded.paths = Module._nodeModulePaths(path.dirname(remindersPath));
const nativeRequire = createRequire(import.meta.url);
loaded.require = (specifier) => {
  if (specifier === "expo-notifications") return notificationMock;
  if (specifier === "@react-native-async-storage/async-storage") {
    return { __esModule: true, default: { getItem: async (key) => key === "ip_local_reminders" ? enabled : null,
      setItem: async () => {}, removeItem: async () => {} } };
  }
  if (specifier === "./api") return { getTasks: async () => {
    taskFetches += 1;
    if (fetchTasksMock) return fetchTasksMock(taskFetches);
    if (beforeResolveTasks) {
      const wait = beforeResolveTasks;
      beforeResolveTasks = null;
      await wait;
    }
    return tasks;
  } };
  if (specifier === "./format") return { parseDate: (value) => {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  } };
  return nativeRequire(specifier);
};
loaded._compile(ts.transpileModule(remindersSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, remindersPath);
const reminders = loaded.exports;

function dueIn(days, hour = 23) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  date.setHours(hour, 59, 0, 0);
  return date.toISOString();
}

function task(overrides = {}) {
  return { title: "Read chapter", course: "Biology", due_date: dueIn(3), ...overrides };
}

function reset() {
  enabled = "1";
  tasks = [];
  taskFetches = 0;
  scheduled = [];
  nextId = 0;
  beforeResolveTasks = null;
  fetchTasksMock = null;
}

test("refresh after a successful create schedules the new task", async () => {
  reset();
  tasks = [task()]; // The successful create is now in the server's canonical list.
  assert.equal(await reminders.refreshAndSyncReminders(), 1);
  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0].content.body, "Read chapter");
});

test("refresh after an edit cancels the old reminder and schedules the updated task", async () => {
  reset();
  tasks = [task()];
  await reminders.refreshAndSyncReminders();
  const originalIdentifier = scheduled[0].identifier;

  tasks = [task({ title: "Read the next chapter", due_date: dueIn(4) })];
  assert.equal(await reminders.refreshAndSyncReminders(), 1);
  assert.equal(scheduled.length, 1);
  assert.notEqual(scheduled[0].identifier, originalIdentifier);
  assert.equal(scheduled[0].content.body, "Read the next chapter");
  assert.equal(scheduled[0].content.data.task.due_date, tasks[0].due_date);
});

test("refresh after delete, completion, and restore removes and rebuilds reminders", async () => {
  reset();
  tasks = [task()];
  await reminders.refreshAndSyncReminders();
  assert.equal(scheduled.length, 1);

  tasks = [];
  assert.equal(await reminders.refreshAndSyncReminders(), 0);
  assert.equal(scheduled.length, 0);

  tasks = [task({ dismissed: true })];
  assert.equal(await reminders.refreshAndSyncReminders(), 0);
  assert.equal(scheduled.length, 0);

  tasks = [task({ dismissed: false })];
  assert.equal(await reminders.refreshAndSyncReminders(), 1);
  assert.equal(scheduled.length, 1);
});

test("disabled reminders skip the task fetch and scheduling", async () => {
  reset();
  enabled = "0";
  tasks = [task()];
  assert.equal(await reminders.refreshAndSyncReminders(), 0);
  assert.equal(taskFetches, 0);
  assert.equal(scheduled.length, 0);
});

test("a task response racing sign-out cannot restore task notifications", async () => {
  reset();
  tasks = [task()];
  let release;
  beforeResolveTasks = new Promise((resolve) => { release = resolve; });
  const pending = reminders.refreshAndSyncReminders();
  while (taskFetches === 0) await new Promise((resolve) => setImmediate(resolve));
  reminders.beginReminderSignOut();
  release();
  assert.equal(await pending, 0);
  assert.equal(scheduled.length, 0);
  reminders.endReminderSignOut();
});

test("a slower older mutation refresh cannot overwrite a newer task schedule", async () => {
  reset();
  tasks = [task({ title: "First version" })];
  let releaseFirst;
  fetchTasksMock = (request) => request === 1
    ? new Promise((resolve) => { releaseFirst = () => resolve([task({ title: "First version" })]); })
    : Promise.resolve([task({ title: "Latest version", due_date: dueIn(4) })]);

  const olderRefresh = reminders.refreshAndSyncReminders();
  while (taskFetches === 0) await new Promise((resolve) => setImmediate(resolve));
  assert.equal(await reminders.refreshAndSyncReminders(), 1);
  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0].content.body, "Latest version");

  releaseFirst();
  assert.equal(await olderRefresh, 0);
  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0].content.body, "Latest version");
});

test("task mutation flows trigger a canonical reminder refresh on success", () => {
  const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
  const cases = [
    ["app/new-task.tsx", /await quickAddTask\(text\);\s*void refreshAndSyncReminders\(\)/, "quick add"],
    ["app/new-task.tsx", /await createTask\(task\);\s*void refreshAndSyncReminders\(\)/, "create"],
    ["app/edit-task.tsx", /await updateManualTask\(id!, patch\);\s*void refreshAndSyncReminders\(\)/, "edit"],
    ["app/edit-task.tsx", /await deleteManualTask\(id!\);\s*void refreshAndSyncReminders\(\)/, "delete"],
    ["app/(tabs)/tasks.tsx", /await dismissTask\(t\.title\);\s*void refreshAndSyncReminders\(\)/, "task-list complete/restore"],
    ["app/(tabs)/today.tsx", /await dismissTask\(title\);\s*void refreshAndSyncReminders\(\)/, "today complete"],
    ["app/task.tsx", /await dismissTask\(task\.title\);\s*void refreshAndSyncReminders\(\)/, "task-detail complete/restore"],
    ["app/focus.tsx", /await dismissTask\(params\.title\);\s*void refreshAndSyncReminders\(\)/, "focus completion"],
  ];
  for (const [file, pattern, label] of cases) {
    assert.match(read(file), pattern, `${label} should refresh reminders after the server mutation succeeds`);
  }
  assert.match(read("lib/queue.ts"), /if \(taskMutationSent\)[\s\S]*?await refreshAndSyncReminders\(\)/,
    "offline writes should refresh reminders after they reach the server");
});
