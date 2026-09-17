/* AI allowance wall.
 *
 * The server marks any response whose AI call was refused for the monthly
 * allowance with an X-IntelliPlan-Paywall header (growth_glue). Most AI
 * callers already catch the error and show their own "the AI is at its
 * limit" text, so this does not replace anything; it adds the one thing
 * those messages cannot say: where to go next.
 *
 * Wraps fetch once, reads a header, and never alters the response. One
 * dialog per page load -- a wall that reappears on every retry is nagging.
 */
(function () {
  'use strict';
  if (window.__ipPaywallInstalled || typeof window.fetch !== 'function') return;
  window.__ipPaywallInstalled = true;

  var shown = false;
  var nativeFetch = window.fetch;

  function show() {
    if (shown || location.pathname === '/upgrade') return;
    shown = true;
    var dlg = document.createElement('dialog');
    dlg.className = 'ip-paywall';
    dlg.setAttribute('aria-labelledby', 'ipPaywallTitle');
    dlg.innerHTML =
      '<p class="ip-paywall-kicker">Free plan</p>' +
      '<h2 id="ipPaywallTitle">That was this month’s last free AI generation.</h2>' +
      '<p>Your planner, imports and reminders keep working. Pro removes the AI limit, or invite a classmate and you both get a free month.</p>' +
      '<div class="ip-paywall-actions">' +
      '<a class="btn-primary" href="/upgrade">See options</a>' +
      '<button type="button" class="btn-secondary" value="close">Not now</button>' +
      '</div>';
    document.body.appendChild(dlg);
    dlg.querySelector('button').addEventListener('click', function () { dlg.close(); });
    dlg.addEventListener('close', function () { dlg.remove(); });
    if (typeof dlg.showModal === 'function') dlg.showModal();
    else dlg.setAttribute('open', '');
  }

  window.fetch = function () {
    return nativeFetch.apply(this, arguments).then(function (res) {
      try {
        if (res && res.headers && res.headers.get('X-IntelliPlan-Paywall')) show();
      } catch (e) { /* a header read must never break the caller */ }
      return res;
    });
  };
})();
