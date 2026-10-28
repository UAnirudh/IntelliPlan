/* Detach this browser's push endpoint before signing out on shared devices. */
(function () {
  'use strict';

  const WAIT_MS = 1800;
  let signingOut = false;

  function withTimeout(promise) {
    let timer;
    return Promise.race([
      promise,
      new Promise((resolve) => { timer = setTimeout(resolve, WAIT_MS); }),
    ]).finally(() => clearTimeout(timer));
  }

  async function removeCurrentBrowserSubscription(userId) {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    try {
      const registration = await withTimeout(navigator.serviceWorker.getRegistration('/'));
      if (!registration || !registration.pushManager) return;
      const subscription = await registration.pushManager.getSubscription();
      if (!subscription) return;
      const endpoint = subscription.endpoint;
      if (userId) {
        try { localStorage.setItem(`ip_push_restore_user_${userId}`, '1'); } catch (_) {}
      }
      try {
        await withTimeout(fetch('/push/unsubscribe', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ endpoint }),
          }));
      } finally {
        // The local unsubscribe also prevents delivery if logout is offline.
        try { await subscription.unsubscribe(); } catch (_) { /* best effort */ }
      }
    } catch (_) {
      // Never strand sign-out because push cleanup is unavailable.
    }
  }

  function restoreSavedSubscriptionForCurrentAccount() {
    const userId = document.body && document.body.dataset.userId;
    if (!userId || !window.IPReminders || typeof window.IPReminders.restoreSavedBrowserPush !== 'function') return;
    let shouldRestore = false;
    try { shouldRestore = localStorage.getItem(`ip_push_restore_user_${userId}`) === '1'; } catch (_) {}
    if (shouldRestore) window.IPReminders.restoreSavedBrowserPush(userId);
  }

  function submitLogoutForm(form) {
    if (typeof form.requestSubmit === 'function') {
      form.requestSubmit();
      return;
    }
    const event = new Event('submit', {bubbles: true, cancelable: true});
    if (form.dispatchEvent(event)) form.submit();
  }
  window.IPLogoutPush = {submit: submitLogoutForm};

  if (document.readyState === 'complete') restoreSavedSubscriptionForCurrentAccount();
  else window.addEventListener('load', restoreSavedSubscriptionForCurrentAccount, {once: true});

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (signingOut) {
      event.preventDefault();
      return;
    }
    let path;
    try { path = new URL(form.action, window.location.href).pathname; } catch (_) { return; }
    if (path !== '/logout') return;
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;

    event.preventDefault();
    signingOut = true;
    const userId = document.body && document.body.dataset.userId;
    removeCurrentBrowserSubscription(userId).finally(() => form.submit());
  }, true);
})();
