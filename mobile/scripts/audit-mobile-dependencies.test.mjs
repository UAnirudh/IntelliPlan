import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { evaluateMobileAudit, formatMobileAuditSuccess } from "./audit-mobile-dependencies.mjs";

const cleanAudit = JSON.parse(fs.readFileSync(new URL("./fixtures-npm-audit-clean.json", import.meta.url), "utf8"));
const previousExpoAudit = JSON.parse(fs.readFileSync(new URL("./fixtures-npm-audit-sdk58.json", import.meta.url), "utf8"));
const lock = JSON.parse(fs.readFileSync(new URL("../package-lock.json", import.meta.url), "utf8"));
const allowlist = JSON.parse(fs.readFileSync(new URL("../security-audit-allowlist.json", import.meta.url), "utf8"));

function evaluate(audit = cleanAudit, policy = allowlist, now = new Date("2026-11-17T00:00:00Z"), lockfile = lock) {
  return evaluateMobileAudit({ audit: structuredClone(audit), lock: lockfile, allowlist: policy, now });
}

test("passes a clean npm audit after the Expo build-tool dependency overrides", () => {
  const cleanPolicy = { ...allowlist, packages: {}, acceptedAdvisories: {} };
  const result = evaluate(cleanAudit, cleanPolicy);
  assert.equal(result.ok, true, result.errors.join("\n"));
  assert.deepEqual(result.advisoryIds, []);
  assert.deepEqual(result.vulnerablePackages, []);
  assert.deepEqual(formatMobileAuditSuccess(result, allowlist.reviewBy), [
    "Mobile dependency audit passed: npm audit found no vulnerabilities.",
    "Review by 2026-12-01.",
  ]);
});

test("reports reviewed moderate findings if a temporary exception is needed", () => {
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

test("fails closed on an unapproved high severity finding", () => {
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
  assert.ok(result.errors.some((message) => message.includes("Unlisted vulnerable package: critical-package")));
});

test("accepts only the exact reviewed high advisory, package, node, and lockfile version", () => {
  const audit = structuredClone(cleanAudit);
  audit.vulnerabilities.picomatch = {
    name: "picomatch",
    severity: "high",
    isDirect: false,
    via: [{ url: "https://github.com/advisories/GHSA-fc5q-39xc-vjpf" }],
    effects: [],
    range: "<=4.0.7",
    nodes: ["node_modules/picomatch"],
  };
  audit.metadata.vulnerabilities.high = 1;
  audit.metadata.vulnerabilities.total = 1;
  const policy = {
    reviewBy: "2026-12-01",
    packages: { picomatch: { nodes: ["node_modules/picomatch"], versions: ["4.0.7"] } },
    acceptedAdvisories: {
      "GHSA-FC5Q-39XC-VJPF": {
        severity: "high",
        source: "https://github.com/advisories/GHSA-fc5q-39xc-vjpf",
        reason: "Temporary exception for the unpatched Expo/Metro build toolchain dependency.",
        upstreamStatus: "No patched release is available; recheck by 2026-12-01.",
        packages: ["picomatch"],
      },
    },
  };
  const testLock = { packages: { "node_modules/picomatch": { version: "4.0.7" } } };
  const result = evaluate(audit, policy, new Date("2026-11-17T00:00:00Z"), testLock);
  assert.equal(result.ok, true, result.errors.join("\n"));
  assert.match(formatMobileAuditSuccess(result, policy.reviewBy)[0], /exact, expiring high-severity exception/);

  const wrongVersion = structuredClone(testLock);
  wrongVersion.packages["node_modules/picomatch"].version = "4.0.8";
  assert.equal(evaluate(audit, policy, new Date("2026-11-17T00:00:00Z"), wrongVersion).ok, false);

  const wrongPathAudit = structuredClone(audit);
  wrongPathAudit.vulnerabilities.picomatch.nodes = ["node_modules/other/picomatch"];
  assert.equal(evaluate(wrongPathAudit, policy, new Date("2026-11-17T00:00:00Z"), testLock).ok, false);

  const newAdvisoryAudit = structuredClone(audit);
  newAdvisoryAudit.vulnerabilities.picomatch.via[0].url = "https://github.com/advisories/GHSA-AAAA-BBBB-CCCC";
  assert.equal(evaluate(newAdvisoryAudit, policy, new Date("2026-11-17T00:00:00Z"), testLock).ok, false);
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
