/*
 * Preload bridge.
 *
 * The renderer runs sandboxed with contextIsolation on and no Node access.
 * This file is the entire surface it can reach, and it is deliberately tiny:
 * three functions, each of which validates its input in the main process
 * before doing anything. Nothing here exposes a filesystem path, a shell, or
 * a generic IPC channel — a bridge that forwards arbitrary channel names is
 * the usual way an Electron app ends up with remote code execution.
 *
 * The web app feature-detects `window.intelliplan`. In a browser it is
 * undefined and the app behaves exactly as it always has; in the desktop
 * build it can hand notifications to the OS instead of the Notification API,
 * which is what makes reminders arrive when the window is closed.
 */

'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('intelliplan', {
  /** True whenever the page is running inside the desktop app. */
  isDesktop: true,

  /**
   * Show a native OS notification.
   * Returns false when the platform cannot show one, so the caller can fall
   * back to an in-page toast rather than silently dropping the message.
   */
  notify(payload) {
    const { title, body, url } = payload || {};
    return ipcRenderer.invoke('notify', {
      title: typeof title === 'string' ? title : '',
      body: typeof body === 'string' ? body : '',
      // Only same-app paths. A full URL here would let page script ask the
      // main process to navigate the window anywhere.
      url: typeof url === 'string' && url.startsWith('/') ? url : null,
    });
  },

  /** Platform and version, for the "about" line and for bug reports. */
  info() {
    return ipcRenderer.invoke('app:info');
  },

  /** Used by the offline page's retry button. */
  retry() {
    return ipcRenderer.invoke('app:retry');
  },
});
