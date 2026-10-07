/* The "Reminders are off" line on the Command Center.
 *
 * Rendered only for accounts with no reminder channel on. Dismissing it is
 * remembered in this browser for two weeks, then it may ask once more:
 * long enough not to nag, short enough that "not now" is not "never".
 */
(function () {
  'use strict';

  var bar = document.getElementById('commandRemind');
  if (!bar || !window.IPReminders) return;

  var SNOOZE_KEY = 'ip_remind_snoozed_until';
  var SNOOZE_MS = 14 * 24 * 60 * 60 * 1000;
  var text = document.getElementById('commandRemindText');
  var on = document.getElementById('commandRemindOn');
  var dismiss = document.getElementById('commandRemindDismiss');

  function snoozedUntil() {
    try { return Number(localStorage.getItem(SNOOZE_KEY)) || 0; } catch (e) { return 0; }
  }
  function snooze() {
    try { localStorage.setItem(SNOOZE_KEY, String(Date.now() + SNOOZE_MS)); } catch (e) { /* private mode */ }
  }

  if (snoozedUntil() > Date.now()) return;
  if (!IPReminders.pushSupported()) {
    text.textContent = 'Get an email before sessions and deadlines.';
  }
  bar.hidden = false;

  dismiss.addEventListener('click', function () {
    snooze();
    bar.hidden = true;
  });

  on.addEventListener('click', function () {
    on.disabled = true;
    on.textContent = 'Setting up...';
    IPReminders.enable({ email: true, push: IPReminders.pushSupported() }).then(function (result) {
      on.disabled = false;
      if (!result.saved) {
        on.textContent = 'Try again';
        text.parentNode.dataset.tone = 'warn';
        text.textContent = 'That did not save. Try again.';
        return;
      }
      on.hidden = true;
      dismiss.textContent = 'Close';
      bar.querySelector('strong').textContent = 'Reminders are on.';
      text.textContent = result.push
        ? 'Email and desktop notifications, before sessions and deadlines.'
        : 'By email. ' + (result.pushMessage || '');
    });
  });
})();
