import test from "node:test";
import assert from "node:assert/strict";
import { inspectBundle, parseBundleManifest } from "./audit-android-bundle.mjs";

const appConfig = {
  expo: {
    android: {
      package: "tech.intelliplan.app",
      permissions: ["android.permission.POST_NOTIFICATIONS", "android.permission.CAMERA"],
      blockedPermissions: ["android.permission.RECORD_AUDIO", "android.permission.READ_EXTERNAL_STORAGE"],
    },
  },
};

const validManifest = `<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="tech.intelliplan.app" android:versionCode="31" android:versionName="1.0.0">
  <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="36" />
  <uses-permission android:name="android.permission.CAMERA" />
  <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
  <uses-permission android:name="android.permission.INTERNET" />
</manifest>`;

test("parses bundle identity, target API, and merged permissions", () => {
  assert.deepEqual(parseBundleManifest(validManifest), {
    packageName: "tech.intelliplan.app",
    versionCode: "31",
    versionName: "1.0.0",
    targetApi: "36",
    permissions: [
      "android.permission.CAMERA",
      "android.permission.INTERNET",
      "android.permission.POST_NOTIFICATIONS",
    ],
  });
});

test("accepts a compliant bundle and reports complete permission inventory", () => {
  const checks = inspectBundle({ manifest: parseBundleManifest(validManifest), appConfig });
  assert.equal(checks.some(({ status }) => status === "BLOCKED"), false);
  assert.ok(checks.some(({ status, message }) => status === "PASS" && message.includes("API 36")));
  assert.ok(checks.some(({ status, message }) => status === "PASS" && message.includes("blocked permissions")));
});

test("blocks a bundle targeting below the current Play minimum", () => {
  const manifest = parseBundleManifest(validManifest.replace('android:targetSdkVersion="36"', 'android:targetSdkVersion="35"'));
  const checks = inspectBundle({ manifest, appConfig });
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("target API is 35")));
});

test("blocks a bundle containing a permission explicitly blocked in app.json", () => {
  const manifest = parseBundleManifest(validManifest.replace("</manifest>", '  <uses-permission android:name="android.permission.RECORD_AUDIO" />\n</manifest>'));
  const checks = inspectBundle({ manifest, appConfig });
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("RECORD_AUDIO")));
  assert.ok(checks.some(({ status, message }) => status === "REVIEW" && message.includes("RECORD_AUDIO")));
});

test("reports missing configured permissions and package mismatches", () => {
  const manifest = parseBundleManifest(validManifest
    .replace('package="tech.intelliplan.app"', 'package="tech.other.app"')
    .replace('  <uses-permission android:name="android.permission.CAMERA" />\n', ""));
  const checks = inspectBundle({ manifest, appConfig });
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("tech.other.app")));
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("CAMERA")));
});

test("can make undeclared merged permissions blocking for a strict review", () => {
  const manifest = parseBundleManifest(validManifest.replace("</manifest>", '  <uses-permission android:name="android.permission.VIBRATE" />\n</manifest>'));
  const checks = inspectBundle({ manifest, appConfig, failOnUndeclared: true });
  assert.ok(checks.some(({ status, message }) => status === "BLOCKED" && message.includes("VIBRATE")));
});

test("rejects malformed bundletool manifest output", () => {
  assert.throws(() => parseBundleManifest("bundletool failed"), /does not contain a <manifest>/);
});
