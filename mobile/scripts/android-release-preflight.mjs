#!/usr/bin/env node

import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const mobileDir = dirname(scriptDir);

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isSemver(value) {
  return typeof value === "string" &&
    /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(value);
}

function isAndroidApplicationId(value) {
  return typeof value === "string" && value.length <= 255 &&
    /^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$/.test(value);
}

function isUuid(value) {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function add(checks, pass, passedMessage, blockedMessage) {
  checks.push({ status: pass ? "PASS" : "BLOCKED", message: pass ? passedMessage : blockedMessage });
}

/** Return release preflight results without echoing config values or credentials. */
export function inspectReleaseConfig({ appConfig, easConfig, env = {}, authStateExists = false, easAuthenticated = false }) {
  const checks = [];
  const expo = appConfig?.expo;
  const android = expo?.android;
  const production = easConfig?.build?.production;

  add(checks, isNonEmptyString(expo?.name), "App display name is configured.", "Set expo.name in app.json.");
  add(checks, isNonEmptyString(expo?.slug), "Expo slug is configured.", "Set expo.slug in app.json.");
  add(checks, isAndroidApplicationId(android?.package), "Android application ID is valid.", "Set a valid Android package ID in expo.android.package.");
  add(checks, isSemver(expo?.version), "App version is valid.", "Set expo.version to a three-part version such as 1.0.0.");
  add(checks, Number.isSafeInteger(android?.versionCode) && android.versionCode > 0,
    "Android version code is a positive integer.", "Set expo.android.versionCode to a positive integer.");

  const projectId = expo?.extra?.eas?.projectId;
  add(checks, isUuid(projectId), "EAS project is linked.", "Link the app to an EAS project with npx eas init (extra.eas.projectId is missing or invalid).");

  const apiBase = production?.env?.EXPO_PUBLIC_API_BASE;
  let secureProductionApi = false;
  if (isNonEmptyString(apiBase)) {
    try {
      const parsed = new URL(apiBase);
      secureProductionApi = parsed.protocol === "https:" && !parsed.username && !parsed.password;
    } catch {
      secureProductionApi = false;
    }
  }
  add(checks, secureProductionApi,
    "Production build uses an HTTPS API base URL.",
    "Set build.production.env.EXPO_PUBLIC_API_BASE to a valid HTTPS URL without embedded credentials.");

  add(checks, production?.android?.buildType === "app-bundle",
    "Production Android build outputs an app bundle.", "Set build.production.android.buildType to app-bundle in eas.json.");
  add(checks, production?.autoIncrement === true || production?.autoIncrement === "version",
    "Production builds auto-increment the app version.", "Enable build.production.autoIncrement in eas.json.");
  add(checks, easConfig?.cli?.appVersionSource === "remote",
    "EAS remote versioning is configured.", "Set cli.appVersionSource to remote in eas.json.");

  add(checks, easAuthenticated,
    "EAS CLI confirmed an active account.",
    authStateExists || isNonEmptyString(env.EXPO_TOKEN)
      ? "EAS credentials are present but could not be validated; run npx eas-cli whoami, then sign in again if needed."
      : "No active EAS account detected; set EXPO_TOKEN or sign in with eas login.");

  add(checks, easConfig?.submit?.production?.android?.track === "production",
    "Android production submissions target the Google Play production track.",
    "Set submit.production.android.track to production in eas.json; an internal-track upload is not a public release.");
  if (easConfig?.submit?.production?.android?.releaseStatus === "draft") {
    checks.push({ status: "INFO", message: "Android production uploads stay in draft until manually submitted in Play Console." });
  }
  if (easConfig?.submit?.["closed-test"]?.android?.track === "alpha" &&
      easConfig?.submit?.["closed-test"]?.android?.releaseStatus === "completed") {
    checks.push({ status: "INFO", message: "A closed-testing submit profile targets the alpha track for opted-in testers." });
  }
  return checks;
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

export function main({ env = process.env, statePath = join(homedir(), ".expo", "state.json") } = {}) {
  const appConfig = readJson(join(mobileDir, "app.json"));
  const easConfig = readJson(join(mobileDir, "eas.json"));
  const checks = inspectReleaseConfig({
    appConfig,
    easConfig,
    env,
    authStateExists: existsSync(statePath),
    easAuthenticated: checkEasAuthentication(),
  });

  if (!appConfig) checks.unshift({ status: "BLOCKED", message: "mobile/app.json is missing or invalid JSON." });
  if (!easConfig) checks.unshift({ status: "BLOCKED", message: "mobile/eas.json is missing or invalid JSON." });

  console.log("Android / EAS release preflight");
  for (const check of checks) console.log(`${check.status}: ${check.message}`);
  const blocked = checks.some(({ status }) => status === "BLOCKED");
  console.log(blocked ? "Result: BLOCKED — resolve the checks above before building." : "Result: READY — static checks passed.");
  return blocked ? 1 : 0;
}

/** A cached CLI state file can outlive its token; ask EAS before reporting ready. */
function checkEasAuthentication() {
  const result = spawnSync("npx", ["eas-cli", "whoami"], {
    cwd: mobileDir,
    encoding: "utf8",
    timeout: 20_000,
    windowsHide: true,
    shell: process.platform === "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });
  return result.status === 0 && isNonEmptyString(result.stdout);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  process.exitCode = main();
}
