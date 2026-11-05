import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { evaluateMobileAudit, formatMobileAuditSuccess } from "./audit-mobile-dependencies.mjs";

const cleanAudit = JSON.parse(fs.readFileSync(new URL("./fixtures-npm-audit-clean.json", import.meta.url), "utf8"));
const previousExpoAudit = JSON.parse(fs.readFileSync(new URL("./fixtures-npm-audit-sdk58.json", import.meta.url), "utf8"));
const lock = JSON.parse(fs.readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"));
const allowlist = JSON.parse(fs.readFileSync(new URL("../security-audit-allowlist.json", import.meta.url), "utf8"));

function evaluate(audit = cleanAudit, policy = allowlist, now = new Date("2026-11-05T00:00:00Z")) {
  return evaluateMobileAudit({ audit: structuredClone(audit), lock, allowlist: policy, now });
}

test("passes a clean npm audit after the Expo build-tool dependency overrides", () => {
  const result = evaluate();
  assert.equal(result.ok, true, result.errors.join("\n"));
  assert.deepEqual(result.advisoryIds, []);
  assert.deepEqual(result.vulnerablePackages, []);
  assert.deepEqual(formatMobileAuditSuccess(result, allowlist.reviewBy), [
    "Mobile dependency audit passed: npm audit found no vulnerabilities.",
    "Review by 2026-12-05.",
  ]);
});

test("reports approved moderate findings if the temporary exception is ever needed again", () => {
  assert.deepEqual(formatMobileAuditSuccess({
    advisoryIds: ["GHSA-AAAA-BBBB-CCCC"],
    vulnerablePackages: ["reviewed-tool"],
  }, "2026-12-05"), [
    "Mobile audit passed with reviewed moderate advisories only: GHSA-AAAA-BBBB-CCCC.",
    "Review by 2026-12-05; vulnerable toolchain packages: reviewed-tool.",
  ]);
});

test("fails closed when the previously vulnerable Expo dependency graph returns", () => {
  const result = evaluate(previousExpoAudit);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((message) => message.includes("Unlisted vulnerable package")));
});

test("fails closed on a new unapproved advisory", () => {
  const audit = structuredClone(cleanAudit);
  audit.vulnerabilities["new-transitive-package"] = {
    name: "new-transitive-package",
    severity: "moderate",
    isDirect: false,
    via: [{ url: "https://github.com/advisories/GHSA-AAAA-BBBB-CCCC" }],
    effects: [],
    range: "<1.2.3",
    nodes: ["node_modules/new-transitive-package"],
  };
  audit.metadata.vulnerabilities.moderate = 1;
  audit.metadata.vulnerabilities.total = 1;

  const result = evaluate(audit);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((message) => message.includes("Unlisted vulnerable package: new-transitive-package")));
});

test("fails closed on high severity findings", () => {
  const audit = structuredClone(cleanAudit);
  audit.vulnerabilities["critical-package"] = {
    name: "critical-package",
    severity: "high",
    isDirect: false,
    via: [{ url: "https://github.com/advisories/GHSA-AAAA-BBBB-CCCC" }],
    effects: [],
    range: "<1.2.3",
    nodes: ["node_modules/critical-package"],
  };
  audit.metadata.vulnerabilities.high = 1;
  audit.metadata.vulnerabilities.total = 1;

  const result = evaluate(audit);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((message) => message.includes("only the exact moderate allowlist is permitted")));
});

test("fails when allowlist review date has expired", () => {
  const result = evaluate(cleanAudit, allowlist, new Date("2026-12-06T00:00:00Z"));
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((message) => message.includes("allowlist expired")));
});

test("fails closed on malformed npm audit output", () => {
  const result = evaluate({ metadata: {} });
  assert.equal(result.ok, false);
  assert.match(result.errors[0], /incomplete report/);
});
