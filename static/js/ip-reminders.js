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
  if (window.IPReminders) return;

  /* navigator.serviceWorker.ready never settles when no worker registered,
     which would leave a "Setting up…" button spinning forever. */
  const WORKER_WAIT_MS = 8000;

  function pushApiAvailable() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  }

  function iosNeedsHomeScreen() {
    const appleMobile = /iPhone|iPad|iPod/i.test(navigator.userAgent)
      || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const standalone = navigator.standalone === true
      || (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
    return appleMobile && !standalone;
  }

  function pushUnavailableReason() {
    if (iosNeedsHomeScreen()) return 'ios-home-screen';
    return pushApiAvailable() ? '' : 'unsupported';
  }

  function pushSupported() {
    return pushUnavailableReason() === '';
  }

  function keyBytes(base64Url) {
    const padding = '='.repeat((4 - (base64Url.length % 4)) % 4);
    const raw = atob((base64Url + padding).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (ch) => ch.charCodeAt(0));
  }

  function subscriptionUsesKey(subscription, expectedKey) {
    const key = subscription && subscription.options
      ? subscription.options.applicationServerKey
      : null;
    if (!key) return false;
    const actualKey = new Uint8Array(key);
    return actualKey.length === expectedKey.length
      && actualKey.every((value, index) => value === expectedKey[index]);
  }

  function workerReady() {
    return Promise.race([
      navigator.serviceWorker.ready,
      new Promise((_, reject) => setTimeout(() => reject(new Error('worker timeout')), WORKER_WAIT_MS)),
    ]);
  }

  /* Resolves to { ok: true } or { ok: false, reason } where reason is one of
     'unsupported' | 'blocked' | 'not-configured' | 'failed'. Never rejects. */
  function isDesktopApp() {
    return !!(window.intelliplan && window.intelliplan.isDesktop);
  }

  /* One id per install, so signing in on a second computer adds a row
     instead of replacing the first. */
  function desktopInstallId() {
    const KEY = 'ip_desktopInstallId';
    try {
      let id = localStorage.getItem(KEY);
      if (!id) {
        id = Array.from(crypto.getRandomValues(new Uint8Array(16)),
          (b) => b.toString(16).padStart(2, '0')).join('');
        localStorage.setItem(KEY, id);
      }
      return id;
    } catch (error) { return ''; }
  }

  async function registerDesktop() {
    const installId = desktopInstallId();
    if (!installId) return { ok: false, reason: 'failed' };
    try {
      const saved = await fetch('/push/desktop-register', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ install_id: installId }),
      });
      return saved.ok ? { ok: true } : { ok: false, reason: 'failed' };
    } catch (error) {
      return { ok: false, reason: 'failed' };
    }
  }

  async function subscribePush(permissionRequest) {
    /* The desktop app shows reminders itself (ip-desktop-feed.js) and has
       no push service to subscribe to. Saying yes records this install so
       the server queues reminders for it. */
    if (isDesktopApp()) return registerDesktop();
    if (!pushSupported()) return { ok: false, reason: 'unsupported' };
    try {
      // Permission must be requested during a visible user gesture. The
      // caller can start this promise synchronously from its Enable click,
      // before the async preference lookup below yields control.
      const permission = permissionRequest
        ? await permissionRequest
        : (Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission());
      if (permission !== 'granted') return { ok: false, reason: 'blocked' };

      const registration = await workerReady();
      const keyResponse = await fetch('/push/vapid-public', { credentials: 'same-origin' });
      const key = ((await keyResponse.json()) || {}).key;
      if (!key) return { ok: false, reason: 'not-configured' };

      const applicationServerKey = keyBytes(key);
      // Browsers reject a second subscribe() call while this registration
      // already owns a subscription. Reuse a matching subscription and sync
      // it to the signed-in account so re-enabling reminders is safe. A stale
      // VAPID key must be replaced or the push service will reject delivery.
      let subscription = await registration.pushManager.getSubscription();
      if (subscription && !subscriptionUsesKey(subscription, applicationServerKey)) {
        const removed = await subscription.unsubscribe();
        if (!removed) return { ok: false, reason: 'failed' };
        subscription = null;
      }
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey,
        });
      }
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
    'ios-home-screen': 'On iPhone or iPad, add IntelliPlan to your Home Screen to receive browser reminders.',
    blocked: 'Notifications are blocked for this site. Allow them in your browser settings, then try again.',
    'not-configured': 'Desktop notifications are not available right now.',
    failed: 'Desktop notifications could not be set up on this device.',
  };

  /* Adds the chosen channels to whatever is already on. A student who set up
     SMS in Settings must not lose it by saying yes to email here.
     Resolves to { saved, email, push, pushMessage }. Never rejects. */
  async function enable(wanted, options) {
    const result = { saved: false, email: false, push: false, pushMessage: '' };
    const channels = new Set();

    try {
      const current = await fetch('/api/notifications/preferences', { credentials: 'same-origin' });
      if (!current.ok) {
        result.pushMessage = 'Could not load your saved reminder settings. Try again.';
        return result;
      }
      const preferences = await current.json();
      if (!preferences || !Array.isArray(preferences.channels)) {
        result.pushMessage = 'Could not load your saved reminder settings. Try again.';
        return result;
      }
      preferences.channels.forEach((channel) => channels.add(channel));
    } catch (error) {
      // The POST replaces the full channel list. Never write a partial list
      // when the read fails, or enabling one channel silently disables the
      // student's existing SMS/email preferences.
      result.pushMessage = 'Could not load your saved reminder settings. Try again.';
      return result;
    }

    if (wanted && wanted.push) {
      const push = await subscribePush(options && options.permissionRequest);
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

  async function restoreSavedBrowserPush(userId) {
    if (!userId || !pushSupported() || Notification.permission !== 'granted') return false;
    const restoreKey = `ip_push_restore_user_${userId}`;
    let shouldRestore = false;
    try { shouldRestore = localStorage.getItem(restoreKey) === '1'; } catch (_) { return false; }
    if (!shouldRestore) return false;
    try {
      const response = await fetch('/api/notifications/preferences', {credentials: 'same-origin'});
      if (!response.ok) return false;
      const preferences = await response.json();
      if (!(preferences.channels || []).includes('push')) {
        localStorage.removeItem(restoreKey);
        return false;
      }
      const result = await subscribePush(Promise.resolve('granted'));
      if (result.ok) localStorage.removeItem(restoreKey);
      return result.ok;
    } catch (_) { return false; }
  }

  window.IPReminders = { pushSupported, pushUnavailableReason, subscribePush, registerDesktop, enable, restoreSavedBrowserPush };
})();
