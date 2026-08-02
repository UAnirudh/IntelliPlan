/* ═══════════════════════════════════════════════════════════════════
   ip-gsap.js — the sitewide interaction layer, and the plugin loader the
   UI component library is built on.

   ip-motion.js owns page-level choreography: reveals, entrances,
   parallax, the auto-adoption pass that finds surfaces and groups. This
   file owns the smaller, closer-in motion — what a control does under a
   finger, what a number does when it scrolls into view, what a nav does
   when the active item changes.

   Why it is separate: ip-motion.js is a scanner. It walks the document
   and decides what to animate. This is a set of behaviours bound to
   explicit hooks — a class, a data attribute, or a tag. Mixing the two
   made the scanner harder to reason about every time either grew.

   Contract:
     IPGsap.ready          Promise → gsap, or null if the engine failed
     IPGsap.plugin(name)   Promise → the plugin, lazily fetched and
                           registered. Names match vendor/gsap/*.min.js.
     IPGsap.reduced        true when the OS asks for less motion
     IPGsap.refresh(scope) re-bind after injecting markup
     IPGsap.press(el)      bind press feedback to one element
     IPGsap.countUp(el)    animate an element's number to its value

   Rules this file holds itself to:
     · Nothing here may hide content. Every effect starts from the
       element's natural, visible state and returns to it. A failure
       leaves a static page, never a blank one.
     · prefers-reduced-motion disables movement, not feedback: a pressed
       button still confirms the press, it just does it without travel.
     · [data-no-motion] anywhere up the tree opts a subtree out.
   ══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var doc = document;

  var REDUCED = !!(window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  var VENDOR_BASE = '/static/js/vendor/gsap/';

  /* Press depth is deliberately small. Anything past ~3% reads as the
     button shrinking rather than as the button being pushed, and on a
     dense toolbar it makes neighbours look like they moved too. */
  var PRESS_SCALE = 0.97;
  var PRESS_IN    = 0.09;
  var PRESS_OUT   = 0.34;

  var api = {
    /* Bumped when behaviour changes — /static is served with long-lived
       caching, so this is how a stale bundle is told from a live one. */
    version: 1,
    reduced: REDUCED,
    ready: null,
    gsap: null
  };
  window.IPGsap = api;

  function noMotion(el) {
    return !el || (el.closest && el.closest('[data-no-motion]'));
  }

  /* ── Engine handle ────────────────────────────────────────────────
     ip-motion.js already owns the loader and its timeout backstop. Wait
     on that rather than racing it with a second one, so there is exactly
     one answer to "is the engine up". */
  api.ready = new Promise(function (resolve) {
    function settle() {
      api.gsap = window.gsap || null;
      resolve(api.gsap);
    }
    /* Wait on IPMotion even when window.gsap is already there. The core
       loads first, but ScrollTrigger is registered by ip-motion.js's
       adapter — resolving on window.gsap alone would let the passes
       below build `scrollTrigger` tween vars against an unregistered
       plugin, which GSAP silently drops. */
    if (window.IPMotion && window.IPMotion.ready) {
      window.IPMotion.ready.then(settle, settle);
      return;
    }
    /* ip-motion.js is deferred and normally parses first, but if it is
       missing entirely this must still settle or every behaviour below
       silently never binds. */
    if (window.gsap) { settle(); return; }
    window.setTimeout(settle, 2600);
  });

  /* ── Lazy plugin loader ───────────────────────────────────────────
     Sixteen plugins ship in vendor/gsap/. Loading them all costs ~286KB
     for pages that use three. Each is fetched once, on first ask, and
     the in-flight promise is cached so ten simultaneous callers share
     one request.

     A failed fetch resolves to null rather than rejecting: a component
     that cannot get Draggable should degrade to a plain input, not throw
     inside whatever awaited it. */
  var pluginCache = {};

  api.plugin = function (name) {
    if (pluginCache[name]) return pluginCache[name];

    pluginCache[name] = api.ready.then(function (g) {
      if (!g) return null;
      var globalName = name === 'gsap' ? 'gsap' : name;
      if (window[globalName]) {
        try { g.registerPlugin(window[globalName]); } catch (e) {}
        return window[globalName];
      }
      return new Promise(function (resolve) {
        var s = doc.createElement('script');
        s.src = VENDOR_BASE + name + '.min.js?v=' + (window.__ipAssetV || '1');
        s.async = true;
        var settled = false;
        function done() {
          if (settled) return;
          settled = true;
          var p = window[globalName] || null;
          if (p) { try { g.registerPlugin(p); } catch (e) {} }
          resolve(p);
        }
        s.addEventListener('load', done);
        s.addEventListener('error', done);
        window.setTimeout(done, 4000);
        doc.head.appendChild(s);
      });
    });
    return pluginCache[name];
  };

  /* ── 1. Press feedback on every control ───────────────────────────
     A button that does not acknowledge the press feels broken on touch,
     where there is no hover state to fall back on. This is the single
     highest-value animation on the site and it applies to every control
     rather than to a hand-picked list.

     Bound with pointer events, not mousedown/touchstart, so a stylus and
     a finger behave the same. `pointercancel` matters: a press that
     turns into a scroll must release, or the control stays depressed
     after the finger has gone. */
  function bindPress(el) {
    if (!el || el.__ipgPress || noMotion(el)) return;

    /* Check the engine *before* marking the element. Marking first meant
       that if this ever ran while the engine was still loading, the
       element was flagged as bound with no listeners on it and no later
       refresh() would ever look at it again. */
    var g = api.gsap;
    if (!g) return;
    el.__ipgPress = true;

    function down() {
      if (el.disabled) return;
      g.to(el, {
        scale: REDUCED ? 1 : PRESS_SCALE,
        /* Under reduced motion the scale is gone, so the only thing left
           to say "received" is the brightness dip. Without this the
           control would be entirely silent for those users. */
        filter: 'brightness(0.93)',
        duration: PRESS_IN,
        ease: 'power2.out',
        overwrite: 'auto'
      });
    }
    function up() {
      g.to(el, {
        scale: 1,
        filter: 'brightness(1)',
        duration: REDUCED ? 0.12 : PRESS_OUT,
        ease: REDUCED ? 'power1.out' : 'elastic.out(1, 0.55)',
        overwrite: 'auto',
        /* Leave no inline filter behind: a stuck `filter` creates a
           containing block and a new stacking context, which quietly
           breaks position:fixed children and z-index inside the button. */
        onComplete: function () { g.set(el, { clearProps: 'filter' }); }
      });
    }

    el.addEventListener('pointerdown', down);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointerleave', up);
    el.addEventListener('pointercancel', up);
    /* Keyboard activation deserves the same acknowledgement as a tap. */
    el.addEventListener('keydown', function (e) {
      if (e.key === ' ' || e.key === 'Enter') down();
    });
    el.addEventListener('keyup', up);
    el.addEventListener('blur', up);
  }

  api.press = bindPress;

  var PRESS_SELECTOR = [
    'button',
    '.btn',
    '[role="button"]',
    'a.btn-primary',
    'a.cta-btn',
    '.ipui-btn',
    'summary'
  ].join(',');

  function initPress(scope) {
    var els = (scope || doc).querySelectorAll(PRESS_SELECTOR);
    Array.prototype.forEach.call(els, bindPress);
  }

  /* ── 2. Click bloom ───────────────────────────────────────────────
     A ring that expands from the click point and fades. It reads as the
     control emitting the action rather than the pointer landing on it,
     which is the difference between this and a Material ripple.

     Only on elements that opt in via .btn-primary / [data-bloom]: every
     button blooming is noise, and the primary action is the one worth
     confirming this loudly. */
  function bloom(el, ev) {
    var g = api.gsap;
    if (!g || REDUCED || noMotion(el)) return;

    var rect = el.getBoundingClientRect();
    if (!rect.width) return;

    var ring = doc.createElement('span');
    ring.className = 'ipg-bloom';
    ring.setAttribute('aria-hidden', 'true');
    var x = (ev && ev.clientX ? ev.clientX - rect.left : rect.width / 2);
    var y = (ev && ev.clientY ? ev.clientY - rect.top : rect.height / 2);
    ring.style.left = x + 'px';
    ring.style.top = y + 'px';

    /* The host needs a containing block for the absolutely-positioned
       ring and needs to clip it. Set both only if the element does not
       already say something else, so a deliberately-overflowing control
       is not silently changed. */
    var cs = getComputedStyle(el);
    if (cs.position === 'static') el.style.position = 'relative';
    if (cs.overflow === 'visible') el.style.overflow = 'hidden';

    el.appendChild(ring);

    /* One removal path, reachable from three directions. A ring that
       outlives its tween is an absolutely-positioned element sitting
       inside a button forever, and they stack: every subsequent click
       adds another. Completion is the normal exit, interruption covers a
       tween killed by a second click, and the timer covers the case
       where the ticker is not running at all (a backgrounded tab throttles
       rAF, so neither callback would ever fire). */
    var removed = false;
    function drop() {
      if (removed) return;
      removed = true;
      if (ring.parentNode) ring.parentNode.removeChild(ring);
    }

    var reach = Math.max(rect.width, rect.height) * 2.2;
    g.fromTo(ring,
      { width: 0, height: 0, opacity: 0.5 },
      {
        width: reach, height: reach, opacity: 0,
        duration: 0.62, ease: 'power2.out',
        onComplete: drop,
        onInterrupt: drop
      });
    window.setTimeout(drop, 2000);
  }

  function initBloom(scope) {
    var els = (scope || doc).querySelectorAll('.btn-primary,.cta-btn,[data-bloom]');
    Array.prototype.forEach.call(els, function (el) {
      if (el.__ipgBloom || noMotion(el)) return;
      el.__ipgBloom = true;
      el.addEventListener('pointerdown', function (ev) { bloom(el, ev); });
    });
  }

  /* ── 3. Count-up ──────────────────────────────────────────────────
     Numbers that arrive by counting are read; numbers that are simply
     there are skimmed. Applied to [data-countup] and, via the scan
     below, to stat values the app renders.

     The element's existing text is the target — nothing has to be passed
     in, so a server-rendered figure animates without the template
     knowing this file exists. Formatting (commas, %, ×, currency) is
     preserved by animating only the numeric run inside the string. */
  var NUM_RE = /-?[\d,]*\.?\d+/;

  function countUp(el) {
    var g = api.gsap;
    if (!el || el.__ipgCount) return;
    el.__ipgCount = true;

    var raw = (el.getAttribute('data-countup') || el.textContent || '').trim();
    var match = raw.match(NUM_RE);
    if (!match) return;

    var target = parseFloat(match[0].replace(/,/g, ''));
    if (!isFinite(target)) return;

    var decimals = (match[0].split('.')[1] || '').length;
    var grouped = match[0].indexOf(',') >= 0;
    var prefix = raw.slice(0, match.index);
    var suffix = raw.slice(match.index + match[0].length);

    if (!g || REDUCED || target === 0) { el.textContent = raw; return; }

    var state = { n: 0 };
    function render() {
      var v = state.n.toFixed(decimals);
      if (grouped) v = Number(v).toLocaleString(undefined, {
        minimumFractionDigits: decimals, maximumFractionDigits: decimals
      });
      el.textContent = prefix + v + suffix;
    }
    render();

    /* Longer numbers get a longer count so the per-digit pace stays
       roughly constant — a 4-digit figure racing by in the same 0.6s as
       a 2-digit one just looks like a flicker. */
    var duration = Math.min(1.6, 0.5 + String(Math.round(target)).length * 0.16);

    g.to(state, {
      n: target,
      duration: duration,
      ease: 'power2.out',
      onUpdate: render,
      onComplete: function () { el.textContent = raw; },
      scrollTrigger: window.ScrollTrigger ? {
        trigger: el, start: 'top 92%', once: true
      } : undefined
    });
  }

  api.countUp = countUp;

  function initCountUp(scope) {
    var els = (scope || doc).querySelectorAll(
      '[data-countup],.stat-value,.stat-number,.kpi-value');
    Array.prototype.forEach.call(els, function (el) {
      if (noMotion(el)) return;
      countUp(el);
    });
  }

  /* ── 4. Progress fills ────────────────────────────────────────────
     A bar that is already full when it scrolls into view says nothing
     about how full it is. Filling it on entry makes the proportion the
     thing you notice.

     Reads the width the stylesheet or the template already set, then
     animates from 0 to it — so no template has to be changed and the
     no-JS rendering is the correct final state. */
  function initProgress(scope) {
    if (!window.ScrollTrigger || REDUCED) return;
    var g = api.gsap;
    if (!g) return;

    var els = (scope || doc).querySelectorAll(
      '.progress-fill,.progress-bar__fill,[data-progress-fill]');
    Array.prototype.forEach.call(els, function (el) {
      if (el.__ipgFill || noMotion(el)) return;
      el.__ipgFill = true;
      var target = el.style.width || getComputedStyle(el).width;
      if (!target || target === '0px') return;
      g.fromTo(el, { width: 0 }, {
        width: target, duration: 0.9, ease: 'power3.out',
        scrollTrigger: { trigger: el, start: 'top 95%', once: true }
      });
    });
  }

  /* ── 5. Section headings ──────────────────────────────────────────
     A heading that rises a few pixels as its section arrives marks the
     boundary between sections without a rule or a gap. Kept to headings
     inside <main> so the nav and the footer are untouched. */
  function initHeadings(scope) {
    if (!window.ScrollTrigger || REDUCED) return;
    var g = api.gsap;
    if (!g) return;

    var main = (scope || doc).querySelector ? ((scope || doc).querySelector('main') || scope || doc) : doc;
    if (!main.querySelectorAll) return;

    var els = main.querySelectorAll('h2,h3');
    Array.prototype.forEach.call(els, function (el) {
      if (el.__ipgHead || noMotion(el)) return;
      /* Above the fold it would be a flash, not a reveal. */
      var r = el.getBoundingClientRect();
      if (r.top < window.innerHeight) { el.__ipgHead = true; return; }
      el.__ipgHead = true;
      g.from(el, {
        y: 18, opacity: 0, duration: 0.55, ease: 'power2.out',
        scrollTrigger: { trigger: el, start: 'top 94%', once: true }
      });
    });
  }

  /* ── 6. Icon-button hover ─────────────────────────────────────────
     Icon-only controls have no label to widen, so the icon itself is the
     only thing that can respond. A small rotation-free scale keeps the
     glyph legible where a spin would not. */
  function initIconHover(scope) {
    var g = api.gsap;
    if (!g || REDUCED) return;
    var els = (scope || doc).querySelectorAll(
      '.icon-btn,.btn-icon,[data-icon-btn]');
    Array.prototype.forEach.call(els, function (el) {
      if (el.__ipgIcon || noMotion(el)) return;
      el.__ipgIcon = true;
      var glyph = el.querySelector('svg,img,.icon') || el;
      el.addEventListener('pointerenter', function (ev) {
        if (ev.pointerType === 'touch') return;
        g.to(glyph, { scale: 1.14, duration: 0.22, ease: 'back.out(2)' });
      });
      el.addEventListener('pointerleave', function () {
        g.to(glyph, { scale: 1, duration: 0.28, ease: 'power2.out' });
      });
    });
  }

  /* ── Wire-up ──────────────────────────────────────────────────────
     Each pass is wrapped: one failing selector must not stop the rest
     from binding. */
  function scan(scope) {
    try { initPress(scope); }     catch (e) {}
    try { initBloom(scope); }     catch (e) {}
    try { initCountUp(scope); }   catch (e) {}
    try { initProgress(scope); }  catch (e) {}
    try { initHeadings(scope); }  catch (e) {}
    try { initIconHover(scope); } catch (e) {}
  }

  api.refresh = function (scope) {
    api.ready.then(function () { scan(scope || doc); });
  };

  /* Most of this app renders after a fetch, so a single pass at boot
     finds an empty <main>. ip-motion.js already runs a debounced
     MutationObserver for exactly this reason; rather than start a second
     one, piggyback on its refresh by observing the same way but only for
     the behaviours above.

     childList only — every pass here sets properties and marks elements
     with expando flags, so watching attributes would feed itself. */
  function watch() {
    if (!window.MutationObserver || !doc.body) return;
    var pending = 0;
    new MutationObserver(function (records) {
      if (pending) return;
      var relevant = false;
      for (var i = 0; i < records.length && !relevant; i++) {
        var added = records[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          if (added[j].nodeType === 1) { relevant = true; break; }
        }
      }
      if (!relevant) return;
      pending = window.setTimeout(function () {
        pending = 0;
        try { scan(doc); } catch (e) {}
      }, 200);
    }).observe(doc.body, { childList: true, subtree: true });
  }

  function boot() {
    api.ready.then(function () {
      scan(doc);
      try { watch(); } catch (e) {}
    });
  }

  if (doc.readyState !== 'loading') boot();
  else doc.addEventListener('DOMContentLoaded', boot);
})();
