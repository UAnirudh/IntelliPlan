import test from "node:test";
import assert from "node:assert/strict";
import { inspectIOSReleaseConfig } from "./ios-release-preflight.mjs";

const validApp = {
  expo: {
    name: "IntelliPlan",
    slug: "intelliplan",
    version: "1.2.3",
    ios: {
      bundleIdentifier: "tech.intelliplan.app",
      buildNumber: "14",
      supportsTablet: true,
      infoPlist: {
        NSCameraUsageDescription: "Take a photo of a problem.",
        NSPhotoLibraryUsageDescription: "Choose a photo of a problem.",
      },
      privacyManifests: {
        NSPrivacyTracking: false,
        NSPrivacyAccessedAPITypes: [{
          NSPrivacyAccessedAPIType: "NSPrivacyAccessedAPICategoryUserDefaults",
          NSPrivacyAccessedAPITypeReasons: ["CA92.1"],
        }],
      },
    },
    extra: { eas: { projectId: "123e4567-e89b-42d3-a456-426614174000" } },
    plugins: ["expo-notifications"],
  },
};

const validEas = {
  cli: { appVersionSource: "remote" },
  build: {
    production: {
      autoIncrement: true,
      distribution: "store",
      ios: { simulator: false },
      env: { EXPO_PUBLIC_API_BASE: "https://api.example.com" },
    },
  },
  submit: { production: { ios: { ascAppId: "1234567890", appleTeamId: "A1B2C3D4E5" } } },
};

test("configured iOS release passes static checks without claiming a store-ready build", () => {
  const checks = inspectIOSReleaseConfig({
    appConfig: validApp,
    easConfig: validEas,
    env: { EXPO_TOKEN: "secret-token" },
  });
  assert.equal(checks.filter(({ status }) => status === "BLOCKED").length, 0);
  assert.ok(checks.some(({ status, message }) => status === "PASS" && message.includes("privacy manifest")));
  assert.ok(checks.some(({ status, message }) => status === "INFO" && message.includes("Xcode 26") && message.includes("April 2027") && message.includes("Xcode 27")));
  assert.ok(checks.some(({ status, message }) => status === "INFO" && message.includes("Apple push credentials")));
  assert.ok(checks.some(({ status, message }) => status === "INFO" && message.includes("signed build")));
  assert.ok(checks.every(({ message }) => !message.includes("secret-token")));
});

test("missing project linkage and EAS auth are blocked without inventing identifiers", () => {
  const appConfig = structuredClone(validApp);
  delete appConfig.expo.extra.eas.projectId;
  const checks = inspectIOSReleaseConfig({ appConfig, easConfig: validEas });
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("npx eas init")));
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("EXPO_TOKEN")));
});

test("invalid identity, production API, platform build settings and permission strings block release", () => {
  const appConfig = structuredClone(validApp);
  const easConfig = structuredClone(validEas);
  appConfig.expo.ios.bundleIdentifier = "not a bundle id";
  appConfig.expo.ios.buildNumber = "one";
  appConfig.expo.ios.infoPlist.NSCameraUsageDescription = "";
  easConfig.build.production.env.EXPO_PUBLIC_API_BASE = "http://localhost:5000";
  easConfig.build.production.distribution = "internal";
  easConfig.build.production.ios.simulator = true;
  const checks = inspectIOSReleaseConfig({ appConfig, easConfig, authStateExists: true });
  assert.equal(checks.filter(({ status }) => status === "BLOCKED").length, 6);
});

test("malformed privacy manifest is blocked and secrets are never echoed", () => {
  const appConfig = structuredClone(validApp);
  appConfig.expo.ios.privacyManifests.NSPrivacyAccessedAPITypes[0].NSPrivacyAccessedAPITypeReasons = [];
  const easConfig = structuredClone(validEas);
  easConfig.submit.production.ios.ascAppId = "secret-app-id";
  const checks = inspectIOSReleaseConfig({ appConfig, easConfig, env: { EXPO_TOKEN: "secret-token" }, authStateExists: true });
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("privacyManifests")));
  assert.ok(checks.every(({ message }) => !message.includes("secret-app-id") && !message.includes("secret-token")));
});

test("missing App Store IDs are owner setup notes and do not become guessed values", () => {
  const easConfig = structuredClone(validEas);
  delete easConfig.submit.production.ios;
  const checks = inspectIOSReleaseConfig({ appConfig: validApp, easConfig, authStateExists: true });
  assert.ok(checks.some(({ status, message }) => status === "INFO" && message.includes("owner-provided App Store Connect app ID")));
  assert.ok(checks.some(({ status, message }) => status === "INFO" && message.includes("owner-provided Apple team ID")));
  assert.ok(checks.every(({ status }) => status !== "BLOCKED"));
});
