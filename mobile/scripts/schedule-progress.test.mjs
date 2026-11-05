import assert from "node:assert/strict";
import fs from "node:fs";
import Module from "node:module";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "..");

function loadProgressHelpers() {
  const filename = path.join(root, "lib", "schedule-progress.ts");
  const loaded = new Module(filename);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(
    ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText,
    filename,
  );
  return loaded.exports;
}

test("rapid completion toggles compose against the latest weekly progress", () => {
  const { toggleScheduleBlockProgress } = loadProgressHelpers();
  const first = toggleScheduleBlockProgress({}, "block-a");
  const second = toggleScheduleBlockProgress(first, "block-b");
  const third = toggleScheduleBlockProgress(second, "block-a");

  assert.deepEqual(second, { "block-a": { done: true }, "block-b": { done: true } });
  assert.deepEqual(third, { "block-a": { done: false }, "block-b": { done: true } });
});

test("a schedule response started before a local tap stays stale after saves drain", () => {
  const { canApplyServerProgress } = loadProgressHelpers();
  const requestRevision = 4;
  const revisionAfterTap = 5;

  assert.equal(canApplyServerProgress(requestRevision, revisionAfterTap, 0), false);
  assert.equal(canApplyServerProgress(revisionAfterTap, revisionAfterTap, 0), true);
  assert.equal(canApplyServerProgress(revisionAfterTap, revisionAfterTap, 1), false);
});

test("full-map progress saves run in order and a failed write does not poison later saves", async () => {
  const { createProgressSaveQueue } = loadProgressHelpers();
  const calls = [];
  let finishFirst;
  let shouldFail = false;
  const queue = createProgressSaveQueue(async (progress) => {
    calls.push(structuredClone(progress));
    if (calls.length === 1) await new Promise((resolve) => { finishFirst = resolve; });
    if (shouldFail) throw new Error("offline");
  });

  const firstSnapshot = { "block-a": { done: true } };
  const latestSnapshot = {
    "block-a": { done: true },
    "block-b": { done: true },
  };
  const first = queue(firstSnapshot);
  const second = queue(latestSnapshot);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 1, "second replacement must wait for the first request");
  finishFirst();
  await Promise.all([first, second]);
  assert.deepEqual(calls, [firstSnapshot, latestSnapshot]);

  shouldFail = true;
  await assert.rejects(queue(latestSnapshot), /offline/);
  shouldFail = false;
  await queue({ ...latestSnapshot, "block-c": { done: true } });
  assert.equal(calls.length, 4);
});

test("Plan screen toggles from the latest local snapshot and labels completion controls", () => {
  const screen = fs.readFileSync(path.join(root, "app", "(tabs)", "plan.tsx"), "utf8");
  assert.match(screen, /scheduleRequestRevision\.current = progressRevision\.current;\s*return getSchedule\(\)/);
  assert.match(screen, /progressRevision\.current \+= 1/);
  assert.match(screen, /canApplyServerProgress\(\s*scheduleRequestRevision\.current,\s*progressRevision\.current,\s*pendingProgressWrites\.current,/);
  assert.match(screen, /toggleScheduleBlockProgress\(progressRef\.current, key\)/);
  assert.match(screen, /data: r\.data, progress: progressRef\.current/);
  assert.match(screen, /await saveProgress\(next\)/);
  assert.match(screen, /accessibilityRole="checkbox"/);
  assert.match(screen, /accessibilityState=\{\{ checked: ticked \}\}/);
});
