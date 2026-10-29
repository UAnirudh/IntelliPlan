#!/usr/bin/env node

import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const mobileDir = dirname(scriptDir);

function nonEmpty(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isVersion(value) {
  return typeof value === "string" &&
    /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(value);
}

function isBundleIdentifier(value) {
  return typeof value === "string" && value.length <= 255 &&
    /^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/.test(value);
}

function isBuildNumber(value) {
  return typeof value === "string" && /^\d+(?:\.\d+)*$/.test(value);
}

function isUuid(value) {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function add(checks, pass, passedMessage, blockedMessage) {
  checks.push({ status: pass ? "PASS" : "BLOCKED", message: pass ? passedMessage : blockedMessage });
}

function secureHttpsUrl(value) {
  if (!nonEmpty(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password;
  } catch {
    return false;
  }
}

function validPrivacyManifest(manifest) {
  if (!manifest || manifest.NSPrivacyTracking !== false || !Array.isArray(manifest.NSPrivacyAccessedAPITypes)) return false;
  return manifest.NSPrivacyAccessedAPITypes.every((entry) =>
    nonEmpty(entry?.NSPrivacyAccessedAPIType) &&
    Array.isArray(entry?.NSPrivacyAccessedAPITypeReasons) &&
    entry.NSPrivacyAccessedAPITypeReasons.length > 0 &&
    entry.NSPrivacyAccessedAPITypeReasons.every(nonEmpty));
}

/** Static checks only. This never proves a signed build or store submission is ready. */
export function inspectIOSReleaseConfig({ appConfig, easConfig, env = {}, authStateExists = false, easAuthenticated = false }) {
  const checks = [];
  const expo = appConfig?.expo;
  const ios = expo?.ios;
  const production = easConfig?.build?.production;

  add(checks, nonEmpty(expo?.name), "App display name is configured.", "Set expo.name in app.json.");
  add(checks, nonEmpty(expo?.slug), "Expo slug is configured.", "Set expo.slug in app.json.");
  add(checks, isBundleIdentifier(ios?.bundleIdentifier), "iOS bundle identifier is valid.", "Set a valid reverse-DNS expo.ios.bundleIdentifier in app.json.");
  add(checks, isVersion(expo?.version), "App version is valid.", "Set expo.version to a three-part version such as 1.0.0.");
  add(checks, isBuildNumber(ios?.buildNumber), "iOS build number is valid.", "Set expo.ios.buildNumber to a numeric version such as 1 or 1.0.0.");
  add(checks, typeof ios?.supportsTablet === "boolean", "iPad support is explicitly declared.", "Set expo.ios.supportsTablet explicitly to true or false.");

  const projectId = expo?.extra?.eas?.projectId;
  add(checks, isUuid(projectId), "EAS project is linked.", "The owner must link an EAS project with npx eas init; extra.eas.projectId is missing or invalid.");

  add(checks, secureHttpsUrl(production?.env?.EXPO_PUBLIC_API_BASE),
    "Production build uses an HTTPS API base URL.",
    "Set build.production.env.EXPO_PUBLIC_API_BASE to a valid HTTPS URL without embedded credentials.");
  add(checks, production?.distribution === "store",
    "Production build explicitly targets store distribution.",
    "Set build.production.distribution to store in eas.json.");
  add(checks, production?.ios?.simulator === false,
    "Production iOS build explicitly targets physical devices.",
    "Set build.production.ios.simulator to false in eas.json.");
  add(checks, production?.autoIncrement === true || production?.autoIncrement === "buildNumber" || production?.autoIncrement === "version",
    "Production build auto-increments its remote version/build number.",
    "Enable build.production.autoIncrement in eas.json.");
  add(checks, easConfig?.cli?.appVersionSource === "remote",
    "EAS remote versioning is configured.", "Set cli.appVersionSource to remote in eas.json.");

  const plist = ios?.infoPlist;
  add(checks, nonEmpty(plist?.NSCameraUsageDescription),
    "Camera purpose text is configured.", "Add a clear NSCameraUsageDescription because the app uses camera capture.");
  add(checks, nonEmpty(plist?.NSPhotoLibraryUsageDescription),
    "Photo library purpose text is configured.", "Add a clear NSPhotoLibraryUsageDescription because the app imports photos.");
  add(checks, validPrivacyManifest(ios?.privacyManifests),
    "App privacy manifest declares tracking and required-reason API usage.",
    "Add a valid expo.ios.privacyManifests declaration for tracking and required-reason API categories/reasons.");
  const plugins = Array.isArray(expo?.plugins) ? expo.plugins : [];
  const notificationsConfigured = plugins.some((plugin) =>
    (Array.isArray(plugin) ? plugin[0] : plugin) === "expo-notifications");
  add(checks, notificationsConfigured,
    "Expo notifications plugin is configured for native notification capabilities.",
    "Add expo-notifications to expo.plugins because the app uses device and push notifications.");

  const submitIOS = easConfig?.submit?.production?.ios;
  if (nonEmpty(submitIOS?.ascAppId) && /^\d+$/.test(submitIOS.ascAppId)) {
    checks.push({ status: "PASS", message: "Production submission profile has an App Store Connect app ID." });
  } else {
    checks.push({ status: "INFO", message: "Add the owner-provided App Store Connect app ID (ascAppId) after creating the app record; interactive EAS Submit can prompt for it." });
  }
  if (nonEmpty(submitIOS?.appleTeamId) && /^[A-Z0-9]{10}$/.test(submitIOS.appleTeamId)) {
    checks.push({ status: "PASS", message: "Production submission profile has an Apple team ID." });
  } else {
    checks.push({ status: "INFO", message: "Add the owner-provided Apple team ID when configuring the submission profile; do not guess it." });
  }

  add(checks, easAuthenticated,
    "EAS CLI confirmed an active account.",
    authStateExists || nonEmpty(env.EXPO_TOKEN)
      ? "EAS credentials are present but could not be validated; run npx eas-cli whoami, then sign in again if needed."
      : "No active EAS account detected; the owner must set EXPO_TOKEN or sign in with eas login.");

  checks.push({ status: "INFO", message: "Before upload, verify the date-specific Apple SDK requirement in the EAS build log: Xcode 26 / iOS 26 since April 28, 2026; from April 2027, Xcode 27 / iOS 27 and an iOS 15 or later deployment target. Confirm the Expo SDK and EAS image support it." });
  checks.push({ status: "INFO", message: "The notifications plugin creates the APNs entitlement; EAS still needs the owner's Apple push credentials, and remote push must be tested on a physical TestFlight build." });
  checks.push({ status: "INFO", message: "Static checks do not verify Apple signing credentials, a signed build or built IPA, privacy disclosures, screenshots, review access, or legal approval." });
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
  const checks = inspectIOSReleaseConfig({
    appConfig,
    easConfig,
    env,
    authStateExists: existsSync(statePath),
    easAuthenticated: checkEasAuthentication(),
  });
  if (!appConfig) checks.unshift({ status: "BLOCKED", message: "mobile/app.json is missing or invalid JSON." });
  if (!easConfig) checks.unshift({ status: "BLOCKED", message: "mobile/eas.json is missing or invalid JSON." });

  console.log("iOS / App Store static release preflight");
  for (const check of checks) console.log(`${check.status}: ${check.message}`);
  const blocked = checks.some(({ status }) => status === "BLOCKED");
  console.log(blocked
    ? "Result: BLOCKED — complete the owner/EAS setup before building."
    : "Result: STATIC CHECKS PASSED — a signed build and App Store review are still required.");
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
  return result.status === 0 && nonEmpty(result.stdout);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  process.exitCode = main();
}
