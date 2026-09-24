/* IntelliPlan — phone app shell (see static/css/ip-app-shell.css).
 *
 * 1. Fills the Menu sheet from the sidebar, so the sidebar customizer's
 *    order and hidden items carry over and there is one list to maintain.
 * 2. Opens/closes the sheet: scrim tap, close button, Escape, swipe down.
 *    Focus moves into the sheet and back to the Menu tab on close.
 * 3. Tucks the tab bar away while the on-screen keyboard is up, so it
 *    never floats over the field being typed into.
 *
 * Vanilla, no dependencies, safe to load on every page: it does nothing
 * unless the tab bar is on the page.
 */
(function () {
  'use strict';

  var bar = document.getElementById('ipTabbar');
  var sheet = document.getElementById('ipMenuSheet');
  var scrim = document.getElementById('ipMenuScrim');
  var openBtn = document.getElementById('ipMenuOpen');
  var closeBtn = document.getElementById('ipMenuClose');
  if (!bar || !sheet || !scrim || !openBtn) return;

  document.body.classList.add('has-tabbar');

  /* ── 1. Rows from the sidebar ─────────────────────────────────── */
  function buildRows() {
    var host = document.getElementById('ipMenuPages');
    var side = document.getElementById('sideNavList');
    if (!host || !side || host.childElementCount) return;

    var links = side.querySelectorAll('a.side-link[href]');
    for (var i = 0; i < links.length; i++) {
      var src = links[i];
      // The customizer hides items with inline display or the hidden
      // attribute; a hidden sidebar item stays hidden here too.
      if (src.hidden || src.style.display === 'none') continue;

      var row = document.createElement('a');
      row.className = 'ip-menu-row';
      row.setAttribute('role', 'listitem');
      row.href = src.getAttribute('href');
      if (src.classList.contains('active')) row.setAttribute('aria-current', 'page');

      var ic = document.createElement('span');
      ic.className = 'ip-menu-ic';
      var srcIc = src.querySelector('.side-icon svg');
      if (srcIc) ic.appendChild(srcIc.cloneNode(true));

      var txt = document.createElement('span');
      txt.className = 'ip-menu-txt';
      txt.textContent = labelOf(src);

      row.appendChild(ic);
      row.appendChild(txt);

      var badge = src.querySelector('.side-streak-badge:not([hidden]), .side-pet-badge:not([hidden])');
      if (badge && badge.textContent.trim()) {
        var b = document.createElement('span');
        b.className = 'ip-menu-badge';
        b.textContent = badge.textContent.trim();
        row.appendChild(b);
      }
      host.appendChild(row);
    }
  }

  /* The link's own text, without the badge counts nested inside it. */
  function labelOf(a) {
    var out = '';
    for (var n = a.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3) out += n.nodeValue;
    }
    return out.replace(/\s+/g, ' ').trim() || a.textContent.trim();
  }

  /* ── 2. Open / close ──────────────────────────────────────────── */
  var lastFocus = null;

  function open() {
    buildRows();
    lastFocus = document.activeElement;
    scrim.hidden = false;
    sheet.hidden = false;
    document.documentElement.classList.add('ipx-sheet-open');
    openBtn.setAttribute('aria-expanded', 'true');
    var first = sheet.querySelector('.ip-menu-row, .ip-menu-close');
    if (first) first.focus({ preventScroll: true });
  }

  function close() {
    if (sheet.hidden) return;
    scrim.hidden = true;
    sheet.hidden = true;
    sheet.style.transform = '';
    document.documentElement.classList.remove('ipx-sheet-open');
    openBtn.setAttribute('aria-expanded', 'false');
    (lastFocus && lastFocus.focus ? lastFocus : openBtn).focus({ preventScroll: true });
  }

  openBtn.addEventListener('click', function () { sheet.hidden ? open() : close(); });
  scrim.addEventListener('click', close);
  if (closeBtn) closeBtn.addEventListener('click', close);

  // Feedback opens its own panel; the sheet should get out of the way.
  sheet.addEventListener('click', function (e) {
    if (e.target.closest && e.target.closest('[data-ip-feedback-open]')) close();
  });

  document.addEventListener('keydown', function (e) {
    if (sheet.hidden) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key !== 'Tab') return;
    // Keep Tab inside the dialog while it is open.
    var f = sheet.querySelectorAll('a[href], button:not([disabled])');
    if (!f.length) return;
    var firstEl = f[0], lastEl = f[f.length - 1];
    if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
    else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
  });

  // Swipe down on the grab area / header to dismiss, like an iOS sheet.
  var startY = null;
  var head = sheet.querySelector('.ip-menu-head');
  var grab = sheet.querySelector('.ip-menu-grab');
  [head, grab].forEach(function (el) {
    if (!el) return;
    el.addEventListener('touchstart', function (e) { startY = e.touches[0].clientY; }, { passive: true });
    el.addEventListener('touchmove', function (e) {
      if (startY === null) return;
      var dy = Math.max(0, e.touches[0].clientY - startY);
      sheet.style.transform = 'translateY(' + dy + 'px)';
    }, { passive: true });
    el.addEventListener('touchend', function (e) {
      if (startY === null) return;
      var dy = e.changedTouches[0].clientY - startY;
      startY = null;
      if (dy > 90) close(); else sheet.style.transform = '';
    });
  });

  /* ── 3. Keyboard up → bar down ────────────────────────────────── */
  var vv = window.visualViewport;
  if (vv) {
    var check = function () {
      var kb = window.innerHeight - vv.height > 140;
      document.body.classList.toggle('ipx-kb', kb);
    };
    vv.addEventListener('resize', check);
  }
})();
