import assert from "node:assert/strict";
import fs from "node:fs";
import Module, { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "..");
const queryPath = path.join(root, "lib", "useQuery.ts");
const nativeRequire = createRequire(import.meta.url);

function loadQueryHook() {
  const listeners = new Set();
  let focusCallback;
  const effects = [];
  const slots = [];
  let cursor = 0;
  const reactMock = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
      return [slots[index], (next) => { slots[index] = typeof next === "function" ? next(slots[index]) : next; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useCallback(callback) { cursor++; return callback; },
    useEffect(effect) { cursor++; effects.push(effect); },
  };
  const loaded = new Module(queryPath);
  loaded.filename = queryPath;
  loaded.paths = Module._nodeModulePaths(path.dirname(queryPath));
  loaded.require = (specifier) => {
    if (specifier === "react") return { __esModule: true, ...reactMock };
    if (specifier === "react-native") return {
      AppState: { addEventListener: (_event, listener) => {
        listeners.add(listener);
        return { remove: () => listeners.delete(listener) };
      } },
    };
    if (specifier === "expo-router") return { useFocusEffect: (callback) => { focusCallback = callback; } };
    if (specifier === "@react-native-async-storage/async-storage") return {
      __esModule: true,
      default: { setItem: async () => {}, getItem: async () => null, getAllKeys: async () => [] },
    };
    return nativeRequire(specifier);
  };
  loaded._compile(ts.transpileModule(fs.readFileSync(queryPath, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, queryPath);

  return {
    mount(fetcher, opts) {
      cursor = 0;
      const query = loaded.exports.useQuery("test", fetcher, opts);
      for (const effect of effects.splice(0)) effect?.();
      return query;
    },
    focus() { focusCallback?.(); },
    activate() { for (const listener of listeners) listener("active"); },
  };
}

test("focus and foreground revalidation coalesce with initial and manual requests", async () => {
  const hook = loadQueryHook();
  const resolvers = [];
  let fetches = 0;
  const query = hook.mount(() => {
    fetches += 1;
    return new Promise((resolve) => resolvers.push(resolve));
  }, { refreshOnFocus: true, refreshOnForeground: true });

  hook.focus(); // The initial focus is satisfied by the automatic first fetch.
  hook.activate(); // Resume during the initial fetch must reuse that request.
  const manualRefresh = query.refresh();
  assert.equal(fetches, 1);

  resolvers[0]({ version: 1 });
  await manualRefresh;
  await new Promise((resolve) => setImmediate(resolve));

  hook.focus();
  hook.activate();
  assert.equal(fetches, 2);
  resolvers[1]({ version: 2 });
  await new Promise((resolve) => setImmediate(resolve));
});

test("Today, Plan, and Grades opt into focus and foreground refresh", () => {
  for (const relative of ["app/(tabs)/today.tsx", "app/(tabs)/plan.tsx", "app/(tabs)/grades.tsx"]) {
    const source = fs.readFileSync(path.join(root, relative), "utf8");
    assert.match(source, /refreshOnFocus:\s*true/);
    assert.match(source, /refreshOnForeground:\s*true/);
  }
});

test("Today updates its local date on foreground and schedules the next midnight", () => {
  const source = fs.readFileSync(path.join(root, "app/(tabs)/today.tsx"), "utf8");
  assert.match(source, /AppState\.addEventListener\("change"/);
  assert.match(source, /if \(state === "active"\) refreshDate\(\)/);
  assert.match(source, /nextMidnight\.setHours\(24, 0, 0, 25\)/);
  assert.match(source, /\[schedule\.data, todayKey\]/);
});
