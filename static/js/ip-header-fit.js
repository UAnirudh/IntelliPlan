/* Top bar fit. Picks the richest header layout that actually fits:
 *
 *   full      logo + "IntelliPlan" + tab labels
 *   no-brand  logo mark only, tab labels kept
 *   icons     logo mark only, icon-only tabs (labels stay for screen
 *             readers and become tooltips)
 *
 * Measured, not breakpoint-guessed: the nav's width depends on which
 * tabs a user sees, their streak badge, the language, and the font. Each
 * state is applied and measured in the same frame, so nothing flickers.
 * Below 769px the burger drawer owns navigation and this steps aside.
 */
(function () {
  'use strict';

  var MIN_GAP = 28;            // breathing room between logo, nav and actions
  var STATES = ['full', 'no-brand', 'icons'];
  var DESKTOP = window.matchMedia('(min-width: 769px)');

  var header = document.querySelector('body > header');
  if (!header) return;
  var logo = header.querySelector('.logo');
  var nav = header.querySelector('.nav-tabs');
  var actions = header.querySelector('.header-actions');
  if (!logo || !nav) return;

  // Icon-only tabs need a visible hint and an accessible name that
  // survives the label being visually hidden.
  header.querySelectorAll('.nav-tab').forEach(function (tab) {
    var label = tab.querySelector('.nav-label');
    if (label && !tab.title) tab.title = label.textContent.trim();
  });

  // Width of the grid track the nav sits in (column 2).
  function navTrack() {
    var cols = getComputedStyle(header).gridTemplateColumns.split(' ');
    return cols.length >= 3 ? parseFloat(cols[1]) : nav.clientWidth;
  }

  function fits() {
    return nav.scrollWidth + MIN_GAP * 2 <= navTrack();
  }

  function fit() {
    if (!DESKTOP.matches) { header.removeAttribute('data-fit'); return; }
    for (var i = 0; i < STATES.length; i++) {
      header.setAttribute('data-fit', STATES[i]);
      if (fits()) return;
    }
    // Nothing fits: stay on the most compact state.
  }

  var queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    // rAF is paused in background tabs; the timeout makes sure a resize
    // that happens there is still applied, and `queued` never sticks.
    var run = function () { if (!queued) return; queued = false; fit(); };
    requestAnimationFrame(run);
    setTimeout(run, 120);
  }

  if ('ResizeObserver' in window) new ResizeObserver(schedule).observe(header);
  window.addEventListener('resize', schedule);
  // Streak/pet badges unhide after their fetch and widen the nav.
  if ('MutationObserver' in window) {
    new MutationObserver(schedule).observe(nav, { attributes: true, subtree: true, attributeFilter: ['hidden'] });
  }
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
  fit();
})();
