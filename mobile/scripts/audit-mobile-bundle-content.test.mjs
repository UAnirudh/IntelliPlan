import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { inspectBundleSourceMaps } from "./audit-mobile-bundle-content.mjs";

function withExport(files, run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "intelliplan-bundle-audit-"));
  try {
    for (const [relativePath, contents] of Object.entries(files)) {
      const fullPath = path.join(root, relativePath);
      fs.mkdirSync(path.dirname(fullPath), { recursive: true });
      fs.writeFileSync(fullPath, contents);
    }
    return run(root);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

function sourceMap(sources) {
  return JSON.stringify({ version: 3, sources, names: [], mappings: "" });
}

test("passes when source maps contain ordinary app and runtime sources only", () => {
  withExport({
    "bundles/android-abc.js": "/* production bundle */",
    "bundles/android-abc.js.map": sourceMap([
      "index.tsx",
      "../node_modules/react-native/Libraries/Core/InitializeCore.js",
      "../node_modules/expo-notifications/src/Notifications.ts",
      "/node_modules/expo/node_modules/@expo/cli/build/metro-require/require.js",
    ]),
  }, (root) => {
    const result = inspectBundleSourceMaps(root);
    assert.equal(result.ok, true);
    assert.equal(result.mapCount, 1);
    assert.equal(result.totalSources, 4);
    assert.deepEqual(result.packages, []);
  });
});

test("blocks vulnerable package roots in both scoped and nested Metro paths", () => {
  withExport({
    "bundles/android-abc.js": "/* production bundle */",
    "bundles/android-abc.js.map": sourceMap([
      "../node_modules/glob/dist/commonjs/index.js",
      "../../node_modules/minimatch/dist/commonjs/index.js",
    ]),
  }, (root) => {
    const result = inspectBundleSourceMaps(root);
    assert.equal(result.ok, false);
    assert.deepEqual(result.packages, ["glob", "minimatch"]);
    assert.equal(result.packageSourceCounts.glob, 1);
  });
});

test("handles indexed Metro maps and Windows-style paths", () => {
  withExport({
    "bundles/ios-abc.hbc": "hermes bytecode",
    "bundles/ios-abc.hbc.map": JSON.stringify({
      version: 3,
      sections: [{ offset: { line: 0, column: 0 }, map: { version: 3, sources: ["..\\node_modules\\glob\\index.js"], names: [], mappings: "" } }],
    }),
  }, (root) => {
    const result = inspectBundleSourceMaps(root);
    assert.equal(result.ok, false);
    assert.deepEqual(result.packages, ["glob"]);
  });
});

test("fails closed when maps are missing, malformed, or omit source entries", () => {
  withExport({}, (root) => assert.throws(() => inspectBundleSourceMaps(root), /No source maps found/));
  withExport({ "bundles/android.js": "bundle", "bundles/android.js.map": "{" }, (root) => assert.throws(() => inspectBundleSourceMaps(root), /Cannot parse source map/));
  withExport({ "bundles/android.js": "bundle", "bundles/android.js.map": JSON.stringify({ version: 3, sources: [], names: [], mappings: "" }) },
    (root) => assert.throws(() => inspectBundleSourceMaps(root), /no source list/));
});

test("fails closed when any bundle lacks its matching external source map", () => {
  withExport({ "bundles/android.js": "bundle", "bundles/other.js.map": sourceMap(["app.js"]) },
    (root) => assert.throws(() => inspectBundleSourceMaps(root), /no matching external source map/));
});
