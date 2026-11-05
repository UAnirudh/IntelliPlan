import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const mobileRoot = path.resolve(scriptDir, "..");
const lockPath = path.join(mobileRoot, "package-lock.json");
const allowlistPath = path.join(mobileRoot, "security-audit-allowlist.json");
const GHSA_RE = /GHSA-[0-9A-Z-]+/i;

function sorted(values) {
  return [...values].sort();
}

function sameSet(left, right) {
  return JSON.stringify(sorted(left)) === JSON.stringify(sorted(right));
}

function parseAdvisoryId(via) {
  const match = typeof via?.url === "string" ? via.url.match(GHSA_RE) : null;
  return match ? match[0].toUpperCase() : null;
}

function advisoryIdsFor(name, vulnerabilities, errors, visited = new Set()) {
  if (visited.has(name)) return new Set();
  visited.add(name);
  const entry = vulnerabilities[name];
  if (!entry || !Array.isArray(entry.via)) {
    errors.push(`Could not resolve npm audit dependency reference ${name}.`);
    return new Set();
  }

  const ids = new Set();
  for (const via of entry.via) {
    if (typeof via === "string") {
      for (const id of advisoryIdsFor(via, vulnerabilities, errors, visited)) ids.add(id);
      continue;
    }
    const id = parseAdvisoryId(via);
    if (!id) {
      errors.push(`Unrecognized npm audit advisory entry for ${name}.`);
      continue;
    }
    ids.add(id);
  }
  return ids;
}

/** Check a package-lock-only npm audit report against the exact temporary exceptions. */
export function evaluateMobileAudit({ audit, lock, allowlist, now = new Date() }) {
  const errors = [];
  const metadata = audit?.metadata?.vulnerabilities;
  const vulnerabilities = audit?.vulnerabilities;
  if (!metadata || !vulnerabilities || typeof vulnerabilities !== "object") {
    return { ok: false, errors: ["npm audit returned an incomplete report."], advisoryIds: [] };
  }

  if (!allowlist?.packages || !allowlist?.acceptedAdvisories || !allowlist?.reviewBy) {
    return { ok: false, errors: ["The mobile dependency allowlist is incomplete."], advisoryIds: [] };
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(allowlist.reviewBy)) {
    errors.push("The allowlist reviewBy date is invalid.");
  } else if (now.toISOString().slice(0, 10) > allowlist.reviewBy) {
    errors.push(`The mobile dependency allowlist expired on ${allowlist.reviewBy}; review upstream status.`);
  }

  const packageEntries = Object.entries(vulnerabilities);
  const severityCounts = { info: 0, low: 0, moderate: 0, high: 0, critical: 0 };
  const observedPairs = new Set();
  const observedIds = new Set();

  for (const severity of Object.keys(severityCounts)) {
    const count = Number(metadata[severity] || 0);
    if (severity !== "moderate" && count > 0) {
      errors.push(`npm audit reported ${count} ${severity} findings; only the exact moderate allowlist is permitted.`);
    }
  }
  if (Number(metadata.total) !== packageEntries.length) {
    errors.push(`npm audit reported ${metadata.total} vulnerable packages but listed ${packageEntries.length}.`);
  }

  for (const [name, entry] of packageEntries) {
    const severity = String(entry?.severity || "").toLowerCase();
    if (!(severity in severityCounts)) {
      errors.push(`Unknown severity for ${name}: ${severity || "missing"}.`);
      continue;
    }
    severityCounts[severity] += 1;
    if (severity !== "moderate") {
      errors.push(`${name} has unapproved ${severity} severity.`);
      continue;
    }

    const packagePolicy = allowlist.packages[name];
    const nodes = Array.isArray(entry.nodes) ? entry.nodes : [];
    if (!packagePolicy) {
      errors.push(`Unlisted vulnerable package: ${name}.`);
      continue;
    }
    if (!sameSet(nodes, packagePolicy.nodes || [])) {
      errors.push(`Unexpected install path for ${name}: ${nodes.join(", ") || "none"}.`);
    }
    for (const node of nodes) {
      const lockedVersion = lock?.packages?.[node]?.version;
      if (!lockedVersion || !(packagePolicy.versions || []).includes(lockedVersion)) {
        errors.push(`Unexpected locked version for ${name} at ${node}: ${lockedVersion || "missing"}.`);
      }
    }

    const ids = advisoryIdsFor(name, vulnerabilities, errors);
    if (ids.size === 0) errors.push(`No advisory ID could be resolved for ${name}.`);
    for (const id of ids) {
      const advisory = allowlist.acceptedAdvisories[id];
      if (!advisory || advisory.severity !== "moderate" || !advisory.packages?.includes(name)) {
        errors.push(`Unapproved advisory/package pair: ${id} affects ${name}.`);
        continue;
      }
      observedIds.add(id);
      observedPairs.add(`${id}:${name}`);
    }
  }

  for (const [severity, actual] of Object.entries(severityCounts)) {
    if (Number(metadata[severity] || 0) !== actual) {
      errors.push(`npm audit ${severity} count mismatch: metadata=${metadata[severity] || 0}, entries=${actual}.`);
    }
  }

  for (const [id, advisory] of Object.entries(allowlist.acceptedAdvisories)) {
    for (const name of advisory.packages || []) {
      if (!observedPairs.has(`${id}:${name}`)) {
        errors.push(`Stale or missing allowlist entry: ${id} no longer affects ${name}.`);
      }
    }
  }

  return {
    ok: errors.length === 0,
    errors,
    advisoryIds: sorted(observedIds),
    vulnerablePackages: sorted(packageEntries.map(([name]) => name)),
  };
}

export function formatMobileAuditSuccess(verdict, reviewBy) {
  if (verdict.vulnerablePackages.length === 0) {
    return [
      "Mobile dependency audit passed: npm audit found no vulnerabilities.",
      `Review by ${reviewBy}.`,
    ];
  }
  return [
    `Mobile audit passed with reviewed moderate advisories only: ${verdict.advisoryIds.join(", ")}.`,
    `Review by ${reviewBy}; vulnerable toolchain packages: ${verdict.vulnerablePackages.join(", ")}.`,
  ];
}

function run() {
  const allowlist = JSON.parse(fs.readFileSync(allowlistPath, "utf8"));
  const lock = JSON.parse(fs.readFileSync(lockPath, "utf8"));
  // npm is a cmd shim on Windows. Invoke cmd explicitly with this fixed
  // command instead of spawnSync(shell: true), which Node warns about.
  const auditCommand = process.platform === "win32" ? process.env.ComSpec || "cmd.exe" : "npm";
  const auditArgs = process.platform === "win32"
    ? ["/d", "/s", "/c", "npm audit --package-lock-only --json"]
    : ["audit", "--package-lock-only", "--json"];
  const result = spawnSync(auditCommand, auditArgs, {
    cwd: mobileRoot,
    encoding: "utf8",
    maxBuffer: 10 * 1024 * 1024,
  });

  if (result.error) {
    console.error(`Could not run npm audit: ${result.error.message}`);
    process.exitCode = 1;
    return;
  }
  let audit;
  try {
    audit = JSON.parse(result.stdout || "");
  } catch {
    console.error("npm audit did not return valid JSON; failing closed.");
    if (result.stderr) console.error(result.stderr.trim());
    process.exitCode = 1;
    return;
  }

  const verdict = evaluateMobileAudit({ audit, lock, allowlist });
  if (!verdict.ok) {
    console.error("Mobile dependency audit failed:");
    for (const error of verdict.errors) console.error(`- ${error}`);
    process.exitCode = 1;
    return;
  }
  for (const message of formatMobileAuditSuccess(verdict, allowlist.reviewBy)) console.log(message);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) run();
