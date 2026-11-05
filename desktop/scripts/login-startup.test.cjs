'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  BACKGROUND_LOGIN_ARG,
  getLoginStartupStatus,
  isBackgroundLoginLaunch,
  setLoginStartup,
} = require('../src/login-startup');

test('startup is opt-in by default on supported platforms', () => {
  const app = { getLoginItemSettings: () => ({ openAtLogin: false, executableWillLaunchAtLogin: false }) };
  assert.deepEqual(getLoginStartupStatus(app, 'win32'), {
    supported: true,
    configured: false,
    active: false,
    approvalRequired: false,
    osDisabled: false,
  });
});

test('enabling startup registers the background launch argument', () => {
  let settings;
  const app = {
    setLoginItemSettings(value) { settings = value; },
    getLoginItemSettings: () => ({ openAtLogin: true, executableWillLaunchAtLogin: true }),
  };
  const state = setLoginStartup(app, 'win32', true);
  assert.equal(settings.openAtLogin, true);
  assert.deepEqual(settings.args, [BACKGROUND_LOGIN_ARG]);
  assert.equal(state.active, true);
});

test('Windows startup status reports when the operating system disabled the entry', () => {
  const state = getLoginStartupStatus({
    getLoginItemSettings: () => ({ openAtLogin: true, executableWillLaunchAtLogin: false }),
  }, 'win32');
  assert.equal(state.configured, true);
  assert.equal(state.active, false);
  assert.equal(state.osDisabled, true);
});

test('macOS approval state is surfaced and login launch starts hidden', () => {
  let queriedWith;
  const app = {
    getLoginItemSettings(options) {
      queriedWith = options;
      return { openAtLogin: true, status: 'requires-approval', wasOpenedAtLogin: true };
    },
  };
  const state = getLoginStartupStatus(app, 'darwin');
  assert.equal(state.approvalRequired, true);
  assert.equal(state.active, false);
  assert.equal(queriedWith.openAsHidden, true);
  assert.equal(isBackgroundLoginLaunch([], app, 'darwin'), true);
});

test('Linux remains unsupported and does not call Electron login-item APIs', () => {
  let called = false;
  const app = {
    getLoginItemSettings() { called = true; },
    setLoginItemSettings() { called = true; },
  };
  assert.deepEqual(getLoginStartupStatus(app, 'linux'), {
    supported: false,
    configured: false,
    active: false,
    approvalRequired: false,
  });
  assert.equal(setLoginStartup(app, 'linux', true).supported, false);
  assert.equal(called, false);
});

test('Windows background login is identified by its launch argument', () => {
  assert.equal(isBackgroundLoginLaunch(['app', BACKGROUND_LOGIN_ARG], null, 'win32'), true);
  assert.equal(isBackgroundLoginLaunch(['app'], null, 'win32'), false);
});
