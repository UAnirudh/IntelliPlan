#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const mobileDir = dirname(scriptDir);
const DEFAULT_MIN_TARGET_API = 36;

function decodeXml(value) {
  return value.replace(/&(?:amp|quot|apos|lt|gt);/g, (entity) => ({
    "&amp;": "&",
    "&quot;": '"',
    "&apos;": "'",
    "&lt;": "<",
    "&gt;": ">",
  })[entity]);
}

function readAttributes(tag) {
  const attributes = {};
  for (const match of tag.matchAll(/([\w:.-]+)\s*=\s*(["'])(.*?)\2/gs)) {
    attributes[match[1]] = decodeXml(match[3]);
  }
  return attributes;
}

/** Read release identity, target SDK and final merged permissions from bundletool XML. */
export function parseBundleManifest(xml) {
  if (typeof xml !== "string") throw new Error("Bundle manifest output is not text.");
  const manifestTag = xml.match(/<manifest\b[^>]*>/s)?.[0];
  if (!manifestTag) throw new Error("bundletool output does not contain a <manifest> element.");
  const manifest = readAttributes(manifestTag);
  const sdkTag = xml.match(/<uses-sdk\b[^>]*>/s)?.[0];
  const sdk = sdkTag ? readAttributes(sdkTag) : {};
  const permissions = new Set();
  for (const match of xml.matchAll(/<uses-permission(?:-[\w-]+)?\b[^>]*>/gs)) {
    const name = readAttributes(match[0])["android:name"];
    if (name) permissions.add(name);
  }
  return {
    packageName: manifest.package || null,
    versionCode: manifest["android:versionCode"] || manifest.versionCode || null,
    versionName: manifest["android:versionName"] || manifest.versionName || null,
    targetApi: sdk["android:targetSdkVersion"] || sdk.targetSdkVersion || null,
    permissions: [...permissions].sort(),
  };
}

/** Compare artifact data with app.json without assuming EAS's remote version code. */
export function inspectBundle({ manifest, appConfig, minTargetApi = DEFAULT_MIN_TARGET_API, failOnUndeclared = false }) {
  const expo = appConfig?.expo;
  const android = expo?.android;
  const results = [];
  const add = (status, message) => results.push({ status, message });

  const expectedPackage = android?.package;
  add(manifest.packageName && manifest.packageName === expectedPackage ? "PASS" : "BLOCKED",
    manifest.packageName === expectedPackage
      ? `Package matches app.json (${manifest.packageName}).`
      : `Bundle package ${manifest.packageName || "is missing"}; expected ${expectedPackage || "a configured expo.android.package"}.`);

  const versionCode = Number(manifest.versionCode);
  add(Number.isSafeInteger(versionCode) && versionCode > 0 ? "PASS" : "BLOCKED",
    Number.isSafeInteger(versionCode) && versionCode > 0
      ? `Bundle version code is ${versionCode}.`
      : "Bundle manifest has no valid positive version code.");

  const targetApi = Number(manifest.targetApi);
  add(Number.isSafeInteger(targetApi) && targetApi >= minTargetApi ? "PASS" : "BLOCKED",
    Number.isSafeInteger(targetApi) && targetApi >= minTargetApi
      ? `Bundle targets Android API ${targetApi} (minimum ${minTargetApi}).`
      : `Bundle target API is ${manifest.targetApi || "missing"}; Google Play release requires at least ${minTargetApi} for this app.`);

  const actualPermissions = new Set(manifest.permissions);
  const required = Array.isArray(android?.permissions) ? android.permissions : [];
  const missing = required.filter((permission) => !actualPermissions.has(permission));
  add(missing.length === 0 ? "PASS" : "BLOCKED",
    missing.length === 0
      ? `Bundle includes all ${required.length} permissions explicitly requested in app.json.`
      : `Bundle is missing configured permissions: ${missing.join(", ")}.`);

  const blocked = Array.isArray(android?.blockedPermissions) ? android.blockedPermissions : [];
  const forbiddenPresent = blocked.filter((permission) => actualPermissions.has(permission));
  add(forbiddenPresent.length === 0 ? "PASS" : "BLOCKED",
    forbiddenPresent.length === 0
      ? `None of the ${blocked.length} blocked permissions appear in the bundle.`
      : `Bundle includes permissions blocked in app.json: ${forbiddenPresent.join(", ")}.`);

  const expected = new Set([...required, "android.permission.INTERNET"]);
  const undeclared = manifest.permissions.filter((permission) => !expected.has(permission));
  if (undeclared.length) {
    add(failOnUndeclared ? "BLOCKED" : "REVIEW",
      `Review merged permissions not explicitly requested in app.json: ${undeclared.join(", ")}.`);
  } else {
    add("PASS", "Every merged permission is explicitly requested in app.json or is Android's baseline INTERNET permission.");
  }

  return results;
}

function parseArgs(argv) {
  const args = { bundle: null, bundletool: process.env.BUNDLETOOL_JAR || null, minTargetApi: DEFAULT_MIN_TARGET_API, failOnUndeclared: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--bundle" || arg === "--bundletool" || arg === "--min-target-api") {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) throw new Error(`${arg} requires a value.`);
      if (arg === "--bundle") args.bundle = value;
      if (arg === "--bundletool") args.bundletool = value;
      if (arg === "--min-target-api") {
        args.minTargetApi = Number(value);
        if (!Number.isSafeInteger(args.minTargetApi) || args.minTargetApi < 1) throw new Error("--min-target-api must be a positive integer.");
      }
      index += 1;
    } else if (arg === "--fail-on-undeclared") {
      args.failOnUndeclared = true;
    } else if (arg === "--help" || arg === "-h") {
      args.help = true;
    } else {
      throw new Error(`Unknown option: ${arg}`);
    }
  }
  return args;
}

function dumpManifest(bundle, bundletool) {
  const bundlePath = resolve(bundle);
  if (!existsSync(bundlePath)) throw new Error(`AAB file not found: ${bundlePath}`);
  const jar = bundletool || process.env.BUNDLETOOL_JAR;
  const command = jar ? "java" : "bundletool";
  const commandArgs = jar
    ? ["-jar", resolve(jar), "dump", "manifest", `--bundle=${bundlePath}`, "--module=base"]
    : ["dump", "manifest", `--bundle=${bundlePath}`, "--module=base"];
  try {
    return execFileSync(command, commandArgs, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  } catch (error) {
    const detail = error.stderr?.toString().trim();
    throw new Error(detail || `Could not run ${command}. Install bundletool and Java, or pass --bundletool <bundletool-all.jar>.`);
  }
}

export function main(argv = process.argv.slice(2)) {
  try {
    const args = parseArgs(argv);
    if (args.help) {
      console.log("Usage: npm run audit:android-bundle -- --bundle <release.aab> [--bundletool <bundletool-all.jar>] [--min-target-api 36] [--fail-on-undeclared]");
      return 0;
    }
    if (!args.bundle || extname(args.bundle).toLowerCase() !== ".aab") {
      throw new Error("Pass an Android App Bundle path with --bundle <release.aab>.");
    }
    const appConfig = JSON.parse(readFileSync(resolve(mobileDir, "app.json"), "utf8"));
    const manifest = parseBundleManifest(dumpManifest(args.bundle, args.bundletool));
    const checks = inspectBundle({ manifest, appConfig, minTargetApi: args.minTargetApi, failOnUndeclared: args.failOnUndeclared });
    console.log("Android App Bundle artifact audit");
    console.log(`Artifact: ${resolve(args.bundle)}`);
    console.log(`Package: ${manifest.packageName || "missing"}`);
    console.log(`Version: ${manifest.versionName || "missing"} (${manifest.versionCode || "missing"})`);
    console.log(`Target API: ${manifest.targetApi || "missing"}`);
    console.log("Merged permissions:");
    for (const permission of manifest.permissions) console.log(`  ${permission}`);
    for (const check of checks) console.log(`${check.status}: ${check.message}`);
    const blocked = checks.some(({ status }) => status === "BLOCKED");
    console.log(blocked ? "Result: BLOCKED — do not submit this artifact." : "Result: REVIEW — artifact checks passed; review permissions and complete the owner/store checks.");
    return blocked ? 1 : 0;
  } catch (error) {
    console.error(`BLOCKED: ${error.message}`);
    return 1;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  process.exitCode = main();
}
