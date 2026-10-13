/* Reminders in the desktop app.
 *
 * The desktop build cannot hold a Web Push subscription, so the server has
 * no way to reach it. This asks instead: once a minute it fetches the
 * reminders that have come due and hands each new one to the operating
 * system through the bridge in desktop/src/preload.js. Closing the window
 * only hides it to the tray, so this keeps running there.
 *
 * Does nothing in a browser, where push already covers the same rows.
 */
(function () {
  'use strict';

  const bridge = window.intelliplan;
  if (!bridge || !bridge.isDesktop || typeof bridge.notify !== 'function') return;

  const POLL_MS = 60 * 1000;
  const FIRST_MS = 8 * 1000;
  const SEEN_KEY = 'ip_desktopFeedSeen';
  /* The feed only reaches back 30 minutes; a few hundred ids is weeks. */
  const SEEN_MAX = 300;

  function loadSeen() {
    try {
      const ids = JSON.parse(localStorage.getItem(SEEN_KEY) || '[]');
      return Array.isArray(ids) ? ids : [];
    } catch (error) { return []; }
  }

  function saveSeen(ids) {
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(ids.slice(-SEEN_MAX))); }
    catch (error) { /* storage full or blocked: worst case one repeat */ }
  }

  /* Set by ip-reminders.js when reminders were turned on in this app. Sent
     with each poll so the server knows the install is still here; an
     uninstalled app stops polling and its reminders stop counting as sent. */
  function installId() {
    try { return localStorage.getItem('ip_desktopInstallId') || ''; }
    catch (error) { return ''; }
  }

  async function poll() {
    let data;
    try {
      const id = installId();
      const url = '/api/notifications/desktop-feed' + (id ? '?install=' + encodeURIComponent(id) : '');
      const response = await fetch(url, { credentials: 'same-origin' });
      if (!response.ok) return;
      data = await response.json();
    } catch (error) { return; }
    if (!data || !Array.isArray(data.notifications)) return;

    const seen = loadSeen();
    const known = new Set(seen);
    let changed = false;
    for (const item of data.notifications) {
      if (!item || known.has(item.id)) continue;
      /* Marked before showing: two windows polling at once would otherwise
         both raise it. */
      known.add(item.id); seen.push(item.id); changed = true;
      try { bridge.notify({ title: item.title, body: item.body, url: item.url }); }
      catch (error) { console.warn('[desktop-feed] notify failed:', error); }
    }
    if (changed) saveSeen(seen);
  }

  setTimeout(() => { poll(); setInterval(poll, POLL_MS); }, FIRST_MS);
  window.IPDesktopFeed = { poll };
})();
