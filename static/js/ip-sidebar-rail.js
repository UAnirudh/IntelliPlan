/* Sidebar rail controller. See ip-sidebar-rail.css for the visual contract.
 *
 * Mode lives in localStorage 'ip_side_mode': 'auto' (default) | 'expanded'
 * | 'collapsed'. It is a per-device preference on purpose: the same student
 * can keep labels visible when they prefer a fixed layout.
 *
 * The <head> bootstrap in base.html applies the same rule before first
 * paint; this file keeps it current on resize and wires the controls.
 * Public API: window.IPSidebar.{getMode,setMode,toggle}.
 */
(function () {
  'use strict';

  var KEY = 'ip_side_mode';
  var MODES = ['auto', 'expanded', 'collapsed'];
  var PEEK_DELAY_MS = 140;
  var root = document.documentElement;
  var sessionMode = null;

  function getMode() {
    if (sessionMode !== null) return sessionMode;
    try {
      var m = localStorage.getItem(KEY);
      return MODES.indexOf(m) >= 0 ? m : 'auto';
    } catch (e) { return 'auto'; }
  }

  function isRail(mode) {
    return mode !== 'expanded';
  }

  function apply() {
    var mode = getMode();
    var rail = isRail(mode);
    if (rail) root.setAttribute('data-side-rail', '');
    else root.removeAttribute('data-side-rail');
    root.setAttribute('data-side-mode', mode);

    if (!rail) endPeek();
    document.dispatchEvent(new CustomEvent('ip:sidebar-mode', { detail: { mode: mode, rail: rail } }));
  }

  function setMode(mode) {
    if (MODES.indexOf(mode) < 0) return;
    sessionMode = mode;
    try { localStorage.setItem(KEY, mode); } catch (e) { /* private mode: session-only */ }
    apply();
  }

  /* The button flips what you see right now. From Auto on a laptop that
     means "pin it open"; from a pinned state it returns to the opposite pin. */
  function toggle() {
    endPeek();
    suppressPeek = true;
    setMode(isRail(getMode()) ? 'expanded' : 'collapsed');
  }

  // ── Peek (hover / focus expands the rail as an overlay) ─────────────
  var side = null;
  var peekTimer = 0;
  var tools = null;
  var keyboardFocus = false;
  var suppressPeek = false;

  function startPeek(immediate) {
    if (!side || suppressPeek || !root.hasAttribute('data-side-rail')) return;
    clearTimeout(peekTimer);
    if (immediate) side.classList.add('is-peek');
    else peekTimer = setTimeout(function () { side.classList.add('is-peek'); }, PEEK_DELAY_MS);
  }

  function endPeek() {
    clearTimeout(peekTimer);
    if (side) side.classList.remove('is-peek');
    if (tools) tools.open = false;
  }

  function wire() {
    side = document.querySelector('body.has-side .app-side');
    tools = document.getElementById('sideTools');
    if (side) {
      side.addEventListener('mouseenter', function () { startPeek(false); });
      side.addEventListener('mouseleave', function () {
        suppressPeek = false;
        if (!keyboardFocus && root.hasAttribute('data-side-rail')) endPeek();
      });
      side.addEventListener('pointerdown', function () {
        keyboardFocus = false;
        suppressPeek = false;
      });
      side.addEventListener('focusin', function (event) {
        keyboardFocus = event.target.matches(':focus-visible');
        if (keyboardFocus) startPeek(true);
      });
      side.addEventListener('focusout', function (event) {
        if (side.contains(event.relatedTarget)) return;
        keyboardFocus = false;
        suppressPeek = false;
        if (!side.matches(':hover')) endPeek();
      });
      side.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        if (tools && tools.open) {
          event.preventDefault();
          tools.querySelector('summary').focus();
          tools.open = false;
        } else if (side.classList.contains('is-peek')) {
          event.preventDefault();
          suppressPeek = true;
          endPeek();
        }
      });
    }
    if (tools) {
      tools.addEventListener('toggle', function () {
        if (tools.open) startPeek(true);
      });
      document.addEventListener('pointerdown', function (event) {
        if (!side.contains(event.target) && !keyboardFocus) endPeek();
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

  // Another tab changed the preference.
  window.addEventListener('storage', function (e) { if (e.key === KEY) { sessionMode = null; apply(); } });

  window.IPSidebar = { getMode: getMode, setMode: setMode, toggle: toggle };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
  else wire();
})();
