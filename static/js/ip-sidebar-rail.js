/* Sidebar rail controller. See ip-sidebar-rail.css for the visual contract.
 *
 * Mode lives in localStorage 'ip_side_mode': 'auto' (default) | 'expanded'
 * | 'collapsed'. It is a per-device preference on purpose: the same student
 * wants the rail on a laptop and the full sidebar on a desktop monitor.
 *
 * The <head> bootstrap in base.html applies the same rule before first
 * paint; this file keeps it current on resize and wires the controls.
 * Public API: window.IPSidebar.{getMode,setMode,toggle}.
 */
(function () {
  'use strict';

  var KEY = 'ip_side_mode';
  var MODES = ['auto', 'expanded', 'collapsed'];
  var LAPTOP = window.matchMedia('(min-width: 769px) and (max-width: 1600px)');
  var PEEK_DELAY_MS = 140;
  var root = document.documentElement;

  function getMode() {
    try {
      var m = localStorage.getItem(KEY);
      return MODES.indexOf(m) >= 0 ? m : 'auto';
    } catch (e) { return 'auto'; }
  }

  function isRail(mode) {
    return mode === 'collapsed' || (mode === 'auto' && LAPTOP.matches);
  }

  function apply() {
    var mode = getMode();
    var rail = isRail(mode);
    if (rail) root.setAttribute('data-side-rail', '');
    else root.removeAttribute('data-side-rail');
    root.setAttribute('data-side-mode', mode);

    var btn = document.getElementById('sideCollapseBtn');
    if (btn) {
      var label = rail ? 'Expand sidebar' : 'Collapse sidebar';
      btn.setAttribute('aria-label', label);
      btn.setAttribute('title', label + ' (Ctrl+\\)');
      btn.setAttribute('aria-expanded', rail ? 'false' : 'true');
    }
    if (!rail) endPeek();
    document.dispatchEvent(new CustomEvent('ip:sidebar-mode', { detail: { mode: mode, rail: rail } }));
  }

  function setMode(mode) {
    if (MODES.indexOf(mode) < 0) return;
    try { localStorage.setItem(KEY, mode); } catch (e) { /* private mode: session-only */ }
    apply();
  }

  /* The button flips what you see right now. From Auto on a laptop that
     means "pin it open"; from a pinned state it returns to the opposite pin. */
  function toggle() {
    setMode(isRail(getMode()) ? 'expanded' : 'collapsed');
  }

  // ── Peek (hover / focus expands the rail as an overlay) ─────────────
  var side = null;
  var peekTimer = 0;

  function startPeek(immediate) {
    if (!side || !root.hasAttribute('data-side-rail')) return;
    clearTimeout(peekTimer);
    if (immediate) side.classList.add('is-peek');
    else peekTimer = setTimeout(function () { side.classList.add('is-peek'); }, PEEK_DELAY_MS);
  }

  function endPeek() {
    clearTimeout(peekTimer);
    if (side) side.classList.remove('is-peek');
  }

  function wire() {
    side = document.querySelector('body.has-side .app-side');
    var btn = document.getElementById('sideCollapseBtn');
    if (btn) btn.addEventListener('click', function (e) { e.preventDefault(); toggle(); });

    if (side) {
      side.addEventListener('mouseenter', function () { startPeek(false); });
      side.addEventListener('mouseleave', function () {
        if (!side.contains(document.activeElement) || document.activeElement === document.body) endPeek();
      });
      // Keyboard users get labels the moment focus enters the rail.
      side.addEventListener('focusin', function (e) {
        if (e.target.matches(':focus-visible')) startPeek(true);
      });
      side.addEventListener('focusout', function (e) {
        if (!side.contains(e.relatedTarget) && !side.matches(':hover')) endPeek();
      });
      side.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && side.classList.contains('is-peek')) {
          endPeek();
          if (document.activeElement && side.contains(document.activeElement)) document.activeElement.blur();
        }
      });
    }

    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key === '\\') {
        e.preventDefault();
        toggle();
      }
    });

    apply();
  }

  if (LAPTOP.addEventListener) LAPTOP.addEventListener('change', apply);
  else if (LAPTOP.addListener) LAPTOP.addListener(apply);

  // Another tab changed the preference.
  window.addEventListener('storage', function (e) { if (e.key === KEY) apply(); });

  window.IPSidebar = { getMode: getMode, setMode: setMode, toggle: toggle };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
  else wire();
})();
