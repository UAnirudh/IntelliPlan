import test from "node:test";
import assert from "node:assert/strict";
import { inspectReleaseConfig } from "./android-release-preflight.mjs";

const validApp = {
  expo: {
    name: "IntelliPlan",
    slug: "intelliplan",
    version: "1.2.3",
    android: { package: "tech.intelliplan.app", versionCode: 7 },
    extra: { eas: { projectId: "123e4567-e89b-42d3-a456-426614174000" } },
  },
};
const validEas = {
  cli: { appVersionSource: "remote" },
  build: {
    production: {
      autoIncrement: true,
      android: { buildType: "app-bundle" },
      env: { EXPO_PUBLIC_API_BASE: "https://api.example.com" },
    },
  },
  submit: {
    "closed-test": { android: { track: "alpha", releaseStatus: "completed" } },
    production: { android: { track: "production", releaseStatus: "draft" } },
  },
};

test("all configured Android production checks pass with EAS auth material", () => {
  const checks = inspectReleaseConfig({ appConfig: validApp, easConfig: validEas, env: { EXPO_TOKEN: "secret-token" }, easAuthenticated: true });
  assert.equal(checks.filter(({ status }) => status === "BLOCKED").length, 0);
  assert.ok(checks.some(({ status, message }) => status === "PASS" && message.includes("confirmed an active account")));
  assert.ok(checks.every(({ message }) => !message.includes("secret-token")));
});

test("missing EAS project linkage and auth are blocking", () => {
  const appConfig = structuredClone(validApp);
  delete appConfig.expo.extra.eas.projectId;
  const checks = inspectReleaseConfig({ appConfig, easConfig: validEas });
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("npx eas init")));
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("No active EAS account")));
});

test("an internal testing track cannot pass as the production submit target", () => {
  const easConfig = structuredClone(validEas);
  easConfig.submit.production.android.track = "internal";
  const checks = inspectReleaseConfig({ appConfig: validApp, easConfig, authStateExists: true, easAuthenticated: true });
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("track to production")));
  assert.ok(checks.some(({ status, message }) => status === "INFO" && message.includes("stay in draft")));
});

test("a closed-test submit profile targets the alpha track for its opt-in testers", () => {
  const checks = inspectReleaseConfig({ appConfig: validApp, easConfig: validEas, authStateExists: true, easAuthenticated: true });
  assert.ok(checks.some(({ status, message }) => status === "INFO" && message.includes("alpha track")));
});

test("a stale auth state file does not count as a verified EAS login", () => {
  const checks = inspectReleaseConfig({ appConfig: validApp, easConfig: validEas, authStateExists: true });
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("could not be validated")));
});

test("bad identity, versions, production API, and build settings are blocking", () => {
  const appConfig = structuredClone(validApp);
  const easConfig = structuredClone(validEas);
  appConfig.expo.android.package = "not a package";
  appConfig.expo.android.versionCode = 0;
  appConfig.expo.version = "soon";
  easConfig.build.production.env.EXPO_PUBLIC_API_BASE = "http://localhost:5000";
  easConfig.build.production.android.buildType = "apk";
  easConfig.build.production.autoIncrement = false;
  easConfig.cli.appVersionSource = "local";
  const checks = inspectReleaseConfig({ appConfig, easConfig, authStateExists: true, easAuthenticated: true });
  assert.equal(checks.filter(({ status }) => status === "BLOCKED").length, 7);
});

test("credentials embedded in the API URL are rejected without echoing them", () => {
  const easConfig = structuredClone(validEas);
  easConfig.build.production.env.EXPO_PUBLIC_API_BASE = "https://user:secret@example.com";
  const checks = inspectReleaseConfig({ appConfig: validApp, easConfig, authStateExists: true, easAuthenticated: true });
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("HTTPS URL")));
  assert.ok(checks.every(({ message }) => !message.includes("secret")));
});
