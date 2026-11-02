import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";

const require = createRequire(import.meta.url);
const queryString = require("query-string");
const mobileRoot = path.resolve(import.meta.dirname, "..");

test("query-string works with the patched ESM decoder through CommonJS", () => {
  const decoderPackage = JSON.parse(
    fs.readFileSync(path.join(mobileRoot, "node_modules", "decode-uri-component", "package.json"), "utf8"),
  );
  assert.equal(decoderPackage.version, "0.5.1");

  const parsed = queryString.parse("?task=Biology%20notes&url=%2Ftask%3Fid%3D123");
  assert.deepEqual({ ...parsed }, {
    task: "Biology notes",
    url: "/task?id=123",
  });
  assert.equal(
    queryString.stringify({ task: "Biology notes", url: "/task?id=123" }),
    "task=Biology%20notes&url=%2Ftask%3Fid%3D123",
  );
});
