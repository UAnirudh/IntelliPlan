/* A one-time, non-blocking reminder setup prompt on the Command Center.
 * Permission is requested only inside the Enable button's user gesture.
 */
(function () {
  'use strict';

  var bar = document.getElementById('commandRemind');
  if (!bar || !window.IPReminders) return;

  var owner = bar.dataset.userId || 'guest';
  var DECISION_KEY = 'ip_remind_prompt_dismissed_' + owner;
  var text = document.getElementById('commandRemindText');
  var on = document.getElementById('commandRemindOn');
  var dismiss = document.getElementById('commandRemindDismiss');

  function hasDecision() {
    try { return localStorage.getItem(DECISION_KEY) === '1'; } catch (e) { return false; }
  }
  function rememberDecision() {
    try { localStorage.setItem(DECISION_KEY, '1'); } catch (e) { /* private mode */ }
  }

  if (hasDecision()) return;
  if (!IPReminders.pushSupported()) {
    text.textContent = IPReminders.pushUnavailableReason() === 'ios-home-screen'
      ? 'On iPhone or iPad, add IntelliPlan to your Home Screen to receive browser reminders. You can enable email reminders here.'
      : 'Get an email before sessions and deadlines.';
  }
  bar.hidden = false;
  // Keep setup secondary to the workspace. Opening the disclosure reveals
  // the explanation; only Enable can request browser permission.
  bar.open = false;

  dismiss.addEventListener('click', function () {
    rememberDecision();
    bar.hidden = true;
  });

  on.addEventListener('click', function () {
    on.disabled = true;
    on.textContent = 'Setting up...';
    // Start the browser permission request before IPReminders performs any
    // async preference reads; browsers may otherwise discard user activation.
    var permissionRequest = null;
    if (IPReminders.pushSupported() && Notification.permission !== 'granted') {
      permissionRequest = Notification.requestPermission();
    }
    IPReminders.enable({ email: true, push: IPReminders.pushSupported() }, {
      permissionRequest: permissionRequest,
    }).then(function (result) {
      on.disabled = false;
      if (!result.saved) {
        on.textContent = 'Try again';
        text.parentNode.dataset.tone = 'warn';
        text.textContent = 'That did not save. Try again.';
        return;
      }
      rememberDecision();
      on.hidden = true;
      dismiss.textContent = 'Close';
      bar.querySelector('strong').textContent = 'Reminders are on.';
      var summary = bar.querySelector('summary');
      if (summary) summary.textContent = 'Reminders are on';
      text.textContent = result.push
        ? 'Email and desktop notifications, before sessions and deadlines.'
        : 'By email. ' + (result.pushMessage || '');
    });
  });
})();
