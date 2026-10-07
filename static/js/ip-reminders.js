/* Turning reminders on, from anywhere that offers it.
 *
 * Onboarding and the Command Center both ask "want reminders?". The answer
 * needs the same three things each time: a push subscription for this
 * browser, the channel flags saved on the account, and the student's UTC
 * offset so quiet hours mean their night and not the server's.
 *
 * Everything hangs off window.IPReminders so nothing here collides with
 * the globals base.html already defines.
 */
(function () {
  'use strict';

  /* navigator.serviceWorker.ready never settles when no worker registered,
     which would leave a "Setting up…" button spinning forever. */
  const WORKER_WAIT_MS = 8000;

  function pushSupported() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  }

  function keyBytes(base64Url) {
    const padding = '='.repeat((4 - (base64Url.length % 4)) % 4);
    const raw = atob((base64Url + padding).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (ch) => ch.charCodeAt(0));
  }

  function workerReady() {
    return Promise.race([
      navigator.serviceWorker.ready,
      new Promise((_, reject) => setTimeout(() => reject(new Error('worker timeout')), WORKER_WAIT_MS)),
    ]);
  }

  /* Resolves to { ok: true } or { ok: false, reason } where reason is one of
     'unsupported' | 'blocked' | 'not-configured' | 'failed'. Never rejects. */
  async function subscribePush() {
    if (!pushSupported()) return { ok: false, reason: 'unsupported' };
    try {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') return { ok: false, reason: 'blocked' };

      const registration = await workerReady();
      const keyResponse = await fetch('/push/vapid-public', { credentials: 'same-origin' });
      const key = ((await keyResponse.json()) || {}).key;
      if (!key) return { ok: false, reason: 'not-configured' };

      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: keyBytes(key),
      });
      const saved = await fetch('/push/subscribe', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subscription: subscription.toJSON() }),
      });
      if (!saved.ok) return { ok: false, reason: 'failed' };
      return { ok: true };
    } catch (error) {
      console.warn('[reminders] push subscribe failed:', error);
      return { ok: false, reason: 'failed' };
    }
  }

  const PUSH_REASONS = {
    unsupported: 'This browser cannot show desktop notifications.',
    blocked: 'Notifications are blocked for this site. Allow them in your browser settings, then try again.',
    'not-configured': 'Desktop notifications are not available right now.',
    failed: 'Desktop notifications could not be set up on this device.',
  };

  /* Adds the chosen channels to whatever is already on. A student who set up
     SMS in Settings must not lose it by saying yes to email here.
     Resolves to { saved, email, push, pushMessage }. Never rejects. */
  async function enable(wanted) {
    const result = { saved: false, email: false, push: false, pushMessage: '' };
    const channels = new Set();

    try {
      const current = await fetch('/api/notifications/preferences', { credentials: 'same-origin' });
      if (current.ok) ((await current.json()).channels || []).forEach((c) => channels.add(c));
    } catch (error) { /* start from an empty set */ }

    if (wanted && wanted.push) {
      const push = await subscribePush();
      if (push.ok) { channels.add('push'); result.push = true; }
      else result.pushMessage = PUSH_REASONS[push.reason] || PUSH_REASONS.failed;
    }
    if (wanted && wanted.email) channels.add('email');

    try {
      const response = await fetch('/api/notifications/preferences', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channels: Array.from(channels),
          utc_offset_minutes: -new Date().getTimezoneOffset(),
        }),
      });
      result.saved = response.ok;
      result.email = response.ok && channels.has('email') && !!(wanted && wanted.email);
      if (!response.ok) result.push = false;
    } catch (error) {
      result.push = false;
    }
    return result;
  }

  window.IPReminders = { pushSupported, subscribePush, enable };
})();
