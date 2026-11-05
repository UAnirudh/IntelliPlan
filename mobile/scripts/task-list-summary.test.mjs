import assert from "node:assert/strict";
import fs from "node:fs";
import Module from "node:module";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "..");

function loadSummary() {
  const filename = path.join(root, "lib", "task-summary.ts");
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

test("Due summary reflects the currently visible filter and course", () => {
  const { taskListSummary } = loadSummary();
  assert.equal(taskListSummary(1, "today"), "1 open item");
  assert.equal(taskListSummary(0, "week"), "0 open items");
  assert.equal(taskListSummary(2, "done"), "2 completed items");
  assert.equal(taskListSummary(2, "all", "Biology"), "2 open items · Biology");
});

test("selected course filter stays visible when the course list shrinks", () => {
  const { shouldShowCourseFilter } = loadSummary();
  assert.equal(shouldShowCourseFilter(3, "__all"), true);
  assert.equal(shouldShowCourseFilter(2, "__all"), false);
  assert.equal(shouldShowCourseFilter(2, "Biology"), true);
  assert.equal(shouldShowCourseFilter(1, "Biology"), true);
});
