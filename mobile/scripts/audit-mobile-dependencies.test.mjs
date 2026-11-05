import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { evaluateMobileAudit } from "./audit-mobile-dependencies.mjs";

const fixtureReport = JSON.parse(fs.readFileSync(new URL("./fixtures-npm-audit-sdk58.json", import.meta.url), "utf8"));
const lockFixture = JSON.parse(fs.readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"));
const allowlist = JSON.parse(fs.readFileSync(new URL("../security-audit-allowlist.json", import.meta.url), "utf8"));
const globId = "GHSA-3G3M-2FJR-6PW4";
const minimatchId = "GHSA-G3HV-75WG-M249";

function fixture() {
  return structuredClone(fixtureReport);
}

function evaluate(audit = fixture(), policy = allowlist, lock = lockFixture, now = new Date("2026-11-05T00:00:00Z")) {
  return evaluateMobileAudit({ audit, lock, allowlist: policy, now });
}

test("passes only the exact reviewed Expo SDK 58 moderate dependency findings", () => {
  const result = evaluate();
  assert.equal(result.ok, true, result.errors.join("\n"));
  assert.deepEqual(result.advisoryIds, [globId, minimatchId]);
});

test("fails on an unlisted advisory even when it appears in an allowlisted package", () => {
  const audit = fixture();
  audit.vulnerabilities.minimatch.via.push({ url: "https://github.com/advisories/GHSA-aaaa-bbbb-cccc" });
  const result = evaluate(audit);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((message) => message.includes("Unapproved advisory/package pair")));
});

test("fails closed on higher severity", () => {
  const audit = fixture();
  audit.metadata.vulnerabilities.moderate -= 1;
  audit.metadata.vulnerabilities.high = 1;
  audit.vulnerabilities["@expo/cli"].severity = "high";
  const result = evaluate(audit);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((message) => message.includes("only the exact moderate allowlist is permitted")));
});

test("fails if a lockfile version changes under the temporary exception", () => {
  const lock = structuredClone(lockFixture);
  lock.packages["node_modules/glob"].version = "14.0.1";
  const result = evaluate(fixture(), allowlist, lock);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((message) => message.includes("Unexpected locked version for glob")));
});

test("fails when an approved advisory disappears so the exception can be removed", () => {
  const audit = fixture();
  audit.vulnerabilities.glob.via = ["minimatch"];
  const result = evaluate(audit);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((message) => message.includes("Stale or missing allowlist entry")));
});

test("fails when allowlist review date has expired", () => {
  const result = evaluate(fixture(), allowlist, lockFixture, new Date("2026-12-06T00:00:00Z"));
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((message) => message.includes("allowlist expired")));
});

test("fails closed on malformed npm audit output", () => {
  const result = evaluate({ metadata: {} });
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /incomplete report/);
});
