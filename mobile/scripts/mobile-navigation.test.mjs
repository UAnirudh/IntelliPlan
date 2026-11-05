import assert from "node:assert/strict";
import fs from "node:fs";
import Module from "node:module";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "..");

function loadNavigation() {
  const filename = path.join(root, "lib", "nav.ts");
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

test("More groups include every destination once and preserve menu order", () => {
  const { MORE_GROUPS, MORE_ITEMS } = loadNavigation();
  const grouped = MORE_GROUPS.flatMap((group) => group.items);
  assert.deepEqual(grouped.map((item) => item.key), MORE_ITEMS.map((item) => item.key));
  assert.equal(new Set(grouped.map((item) => item.key)).size, MORE_ITEMS.length);
  assert.deepEqual(MORE_GROUPS.map((group) => group.label), [
    "Plan & focus",
    "Study",
    "Progress",
    "Explore",
  ]);
});

test("task detail rows expose a concise screen-reader action", () => {
  const source = fs.readFileSync(path.join(root, "components", "TaskRow.tsx"), "utf8");
  assert.match(source, /accessibilityLabel=\{onPress \? `Open \$\{task\.title\} details` : undefined\}/);
  assert.match(source, /accessibilityHint=\{onPress \? "View assignment details" : undefined\}/);
});


test("screen titles remain accessible and do not truncate on narrow displays", () => {
  const source = fs.readFileSync(path.join(root, "components", "Header.tsx"), "utf8");
  assert.ok(source.includes('<T variant="xl" weight="700" accessibilityRole="header">'));
  assert.ok(source.includes('<T variant="sm" tone="secondary">'));
  assert.doesNotMatch(source, /numberOfLines/);
  assert.ok(source.includes('accessibilityRole="image"'));
});

test("Today agenda adapts its time rail and keeps the study action screen-reader clear", () => {
  const filename = path.join(root, "lib", "today-layout.ts");
  const loaded = new Module(filename);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(
    ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText,
    filename,
  );
  const { agendaTimeColumnWidth } = loaded.exports;
  assert.equal(agendaTimeColumnWidth(320), 58);
  assert.equal(agendaTimeColumnWidth(390), 64);
  assert.equal(agendaTimeColumnWidth(430), 72);

  const screen = fs.readFileSync(path.join(root, "app", "(tabs)", "today.tsx"), "utf8");
  assert.match(screen, /accessibilityLabel=\{`Start focused study: \$\{featuredTask\.title\}`\}/);
  assert.match(screen, /accessibilityHint=\{block\.is_break \? undefined : "Starts a focus session for this study block"\}/);
  assert.match(screen, /accessibilityState=\{\{ disabled: !!block\.is_break, selected: !block\.is_break && index === focusedBlockIndex \}\}/);
  assert.match(screen, /accessibilityRole="header">Your day<\/T>/);
});
