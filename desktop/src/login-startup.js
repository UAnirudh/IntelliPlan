'use strict';

const BACKGROUND_LOGIN_ARG = '--intelliplan-background';
const LOGIN_ARGS = [BACKGROUND_LOGIN_ARG];

function isLoginStartupSupported(platform) {
  return platform === 'win32' || platform === 'darwin';
}

function isBackgroundLoginLaunch(argv = process.argv, app = null, platform = process.platform) {
  if (Array.isArray(argv) && argv.includes(BACKGROUND_LOGIN_ARG)) return true;
  if (platform !== 'darwin' || !app || typeof app.getLoginItemSettings !== 'function') return false;
  try {
    const status = app.getLoginItemSettings({ args: LOGIN_ARGS, openAsHidden: true });
    return status.wasOpenedAtLogin === true || status.wasOpenedAsHidden === true;
  } catch {
    return false;
  }
}

function getLoginStartupStatus(app, platform) {
  if (!isLoginStartupSupported(platform)) {
    return { supported: false, configured: false, active: false, approvalRequired: false };
  }

  try {
    const status = app.getLoginItemSettings({ args: LOGIN_ARGS, openAsHidden: true });
    const configured = status.openAtLogin === true;
    const approvalRequired = platform === 'darwin' && status.status === 'requires-approval';
    const osAllowsLaunch = platform === 'win32'
      ? (typeof status.executableWillLaunchAtLogin === 'boolean'
        ? status.executableWillLaunchAtLogin
        : configured)
      : status.status === 'enabled';

    return {
      supported: true,
      configured,
      active: configured && osAllowsLaunch,
      approvalRequired,
      osDisabled: configured && !osAllowsLaunch && !approvalRequired,
    };
  } catch {
    return { supported: true, configured: false, active: false, approvalRequired: false, unavailable: true };
  }
}

function setLoginStartup(app, platform, enabled) {
  if (!isLoginStartupSupported(platform)) {
    return { supported: false, configured: false, active: false, approvalRequired: false };
  }

  try {
    app.setLoginItemSettings({ openAtLogin: enabled === true, args: LOGIN_ARGS, openAsHidden: true });
  } catch {
    return { supported: true, configured: false, active: false, approvalRequired: false, unavailable: true };
  }
  return getLoginStartupStatus(app, platform);
}

module.exports = {
  BACKGROUND_LOGIN_ARG,
  getLoginStartupStatus,
  isBackgroundLoginLaunch,
  isLoginStartupSupported,
  setLoginStartup,
};
