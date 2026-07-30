/* ═══════════════════════════════════════════════════════════════════
   ip-motion.js — driver for ip-motion.css.

   Why Motion One and not hand-rolled rAF: the reveal and stagger work is
   trivial with CSS transitions, but the spring entrances (hero, auth
   panel, chat open) need real spring physics to stop looking like eased
   tweens, and scroll-linked progress needs a scroll() that doesn't fight
   the main thread. Motion is vendored at static/js/vendor/motion.min.js
   rather than pulled from a CDN so it stays inside `script-src 'self'`
   (see the CSP block in App.py) and survives a CDN outage.

   Contract with the rest of the app:
     window.IPMotion.animate / spring / stagger / inView / scroll
     window.IPMotion.ready      — Promise, resolves once Motion is up
     window.IPMotion.reduced    — true when the OS asks for less motion
     window.IPMotion.refresh()  — re-scan after injecting new markup

   Every entry point is null-safe and wrapped, because a thrown error
   here must never take down the page's own scripts.
   ══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var doc  = document;
  var root = doc.documentElement;

  var REDUCED = !!(window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  /* Physics constants, named so the numbers aren't magic at call sites.
     GENTLE is the house default — it settles without visible bounce.
     BOUNCY is only for things the user just asked for (a panel opening). */
  var SPRING_GENTLE = { type: 'spring', stiffness: 170, damping: 26, mass: 1 };
  var SPRING_BOUNCY = { type: 'spring', stiffness: 320, damping: 22, mass: 0.9 };
  var SPRING_SNAPPY = { type: 'spring', stiffness: 460, damping: 34, mass: 0.7 };

  var STAGGER_STEP  = 0.07;   // seconds between siblings in a group
  var STAGGER_CAP   = 0.42;   // never delay the last child past this
  var MAGNET_RANGE  = 0.32;   // how far a magnetic control follows the pointer
  var TILT_MAX_DEG  = 7;

  var api = {
    reduced: REDUCED,
    ready: null,
    springs: { gentle: SPRING_GENTLE, bouncy: SPRING_BOUNCY, snappy: SPRING_SNAPPY }
  };
  window.IPMotion = api;

  /* ── Motion One loader ────────────────────────────────────────────
     The vendored build is UMD, so it attaches window.Motion. Loading it
     with `defer` off a normal <script> would be simpler, but this file
     is itself deferred and page scripts may call IPMotion immediately —
     hence a promise other code can await. */
  api.ready = new Promise(function (resolve) {
    if (window.Motion) { resolve(window.Motion); return; }
    var existing = doc.querySelector('script[data-ipm-vendor]');
    if (!existing) { resolve(null); return; }

    /* If the vendor script already errored before this file parsed, no
       further load/error event will ever fire — and since CSS holds
       [data-ipm-enter] hidden until playEntrance stamps it, a promise
       that never settles would leave those sections permanently blank.
       The timeout is the guarantee that ready always settles. */
    var settled = false;
    function done() {
      if (settled) return;
      settled = true;
      resolve(window.Motion || null);
    }
    existing.addEventListener('load', done);
    existing.addEventListener('error', done);
    window.setTimeout(done, 2500);
  }).then(function (M) {
    if (!M) return null;
    /* Re-export the handful of primitives pages actually use, so callers
       never have to know whether Motion loaded. */
    api.animate = M.animate;
    api.inView  = M.inView;
    api.scroll  = M.scroll;
    api.stagger = M.stagger;
    api.spring  = M.spring;
    return M;
  });

  /* Fallbacks so `IPMotion.animate(...)` is always callable. They no-op
     rather than throw, and return a thenable so `.finished` chains hold. */
  function noopAnimation() {
    var p = Promise.resolve();
    return { finished: p, stop: function () {}, then: p.then.bind(p) };
  }
  api.animate = api.animate || function (el, keyframes, opts) {
    /* Without Motion, land the element on its final state immediately so
       nothing is left mid-transition and invisible. */
    try {
      if (!el) return noopAnimation();
      var nodes = el.length !== undefined && !el.style ? el : [el];
      Array.prototype.forEach.call(nodes, function (n) {
        if (!n || !n.style) return;
        Object.keys(keyframes || {}).forEach(function (k) {
          var v = keyframes[k];
          var last = Array.isArray(v) ? v[v.length - 1] : v;
          if (k === 'opacity') n.style.opacity = last;
        });
      });
    } catch (e) {}
    return noopAnimation();
  };

  /* ── 1. Scroll reveal ─────────────────────────────────────────────
     CSS owns the actual transition; this only decides *when* to add
     .ipm-in and how much to stagger siblings. Keeping the animation in
     CSS means a failed script leaves everything visible (html.ipm-on is
     only ever added once we know we can also remove it). */
  var revealObserver = null;

  function groupDelay(el) {
    var group = el.closest('[data-ipm-group]');
    if (!group) return 0;
    var kids = group.querySelectorAll('[data-ipm-reveal]');
    var i = Array.prototype.indexOf.call(kids, el);
    if (i < 0) return 0;
    return Math.min(i * STAGGER_STEP, STAGGER_CAP);
  }

  function revealNow(el) {
    el.style.setProperty('--ipm-delay', groupDelay(el) + 's');
    el.classList.add('ipm-in');
    /* Drop will-change once the transition is done — see the CSS note. */
    window.setTimeout(function () { el.classList.add('ipm-settled'); }, 1100);
  }

  function initReveal(scope) {
    var els = (scope || doc).querySelectorAll('[data-ipm-reveal]:not(.ipm-in)');
    if (!els.length) return;

    if (REDUCED || !('IntersectionObserver' in window)) {
      Array.prototype.forEach.call(els, function (e) { e.classList.add('ipm-in', 'ipm-settled'); });
      return;
    }
    if (!revealObserver) {
      revealObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          revealNow(en.target);
          revealObserver.unobserve(en.target);
        });
      }, { rootMargin: '0px 0px -10% 0px', threshold: 0.08 });
    }
    Array.prototype.forEach.call(els, function (e) { revealObserver.observe(e); });
  }

  /* ── 2. Magnetic controls ────────────────────────────────────────
     Pointer offset from the element's centre, scaled down, written to
     custom properties. CSS does the transform, so the release easing is
     declarative and we never stomp a transform some other rule set. */
  function bindMagnetic(el) {
    if (el.__ipmMagnet) return;
    el.__ipmMagnet = true;

    var strength = parseFloat(el.getAttribute('data-magnetic')) || MAGNET_RANGE;
    var frame = 0;

    function onMove(ev) {
      if (frame) return;                       // one update per frame, max
      frame = requestAnimationFrame(function () {
        frame = 0;
        var r = el.getBoundingClientRect();
        var dx = (ev.clientX - (r.left + r.width  / 2)) * strength;
        var dy = (ev.clientY - (r.top  + r.height / 2)) * strength;
        el.style.setProperty('--ipm-mx', dx.toFixed(2) + 'px');
        el.style.setProperty('--ipm-my', dy.toFixed(2) + 'px');
      });
    }
    function onLeave() {
      if (frame) { cancelAnimationFrame(frame); frame = 0; }
      el.classList.remove('ipm-tracking');
      el.style.setProperty('--ipm-mx', '0px');
      el.style.setProperty('--ipm-my', '0px');
    }

    el.addEventListener('pointerenter', function (ev) {
      /* Touch has no hover, and a magnetic offset there just makes the
         tap target drift under the finger. */
      if (ev.pointerType === 'touch') return;
      el.classList.add('ipm-tracking');
    });
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerleave', onLeave);
    el.addEventListener('blur', onLeave);
  }

  /* ── 3. Tilt ─────────────────────────────────────────────────────── */
  function bindTilt(el) {
    if (el.__ipmTilt) return;
    el.__ipmTilt = true;

    var max = parseFloat(el.getAttribute('data-tilt')) || TILT_MAX_DEG;
    var frame = 0;

    function onMove(ev) {
      if (frame) return;
      frame = requestAnimationFrame(function () {
        frame = 0;
        var r = el.getBoundingClientRect();
        if (!r.width || !r.height) return;
        var px = (ev.clientX - r.left) / r.width  - 0.5;   // −0.5 … 0.5
        var py = (ev.clientY - r.top)  / r.height - 0.5;
        el.style.setProperty('--ipm-ry', (px *  max * 2).toFixed(2) + 'deg');
        el.style.setProperty('--ipm-rx', (py * -max * 2).toFixed(2) + 'deg');
        el.style.setProperty('--ipm-tz', '-4px');
      });
    }
    function reset() {
      if (frame) { cancelAnimationFrame(frame); frame = 0; }
      el.classList.remove('ipm-tracking');
      el.style.setProperty('--ipm-rx', '0deg');
      el.style.setProperty('--ipm-ry', '0deg');
      el.style.setProperty('--ipm-tz', '0px');
    }

    el.addEventListener('pointerenter', function (ev) {
      if (ev.pointerType === 'touch') return;
      el.classList.add('ipm-tracking');
    });
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerleave', reset);
  }

  /* ── 4. Idle float phase-offset ──────────────────────────────────
     Without this every floating element bobs in lockstep, which reads as
     a glitch rather than as depth. Deterministic offsets (not random) so
     the page looks identical on every load. */
  function initFloat(scope) {
    var els = (scope || doc).querySelectorAll('[data-float]:not([data-ipm-phased])');
    Array.prototype.forEach.call(els, function (el, i) {
      el.setAttribute('data-ipm-phased', '');
      if (!el.style.getPropertyValue('--ipm-float-delay')) {
        el.style.setProperty('--ipm-float-delay', (-(i * 1.35) % 9).toFixed(2) + 's');
      }
      if (!el.style.getPropertyValue('--ipm-float-dur')) {
        el.style.setProperty('--ipm-float-dur', (7.5 + (i % 4) * 0.9).toFixed(1) + 's');
      }
    });
  }

  /* ── 5. Scroll-linked parallax ───────────────────────────────────
     data-parallax="0.2" → element drifts 20% of its own height against
     the scroll. Uses Motion's scroll() progress callback and writes a
     custom property; CSS owns the transform. Going through a variable
     instead of scroll()-driving an animation keeps this working across
     Motion versions and avoids clobbering a transform another rule set
     already put on the element. */
  function initParallax(M, scope) {
    if (!M || !M.scroll || REDUCED) return;
    var els = (scope || doc).querySelectorAll('[data-parallax]:not([data-ipm-bound])');
    Array.prototype.forEach.call(els, function (el) {
      el.setAttribute('data-ipm-bound', '');
      var depth = parseFloat(el.getAttribute('data-parallax')) || 0.15;
      try {
        M.scroll(function (progress) {
          /* progress runs 0→1 across the element's pass through the
             viewport; recentre it so the element sits neutral at
             mid-screen rather than starting pre-offset. */
          var offset = (progress - 0.5) * depth * 200;
          el.style.setProperty('--ipm-py', offset.toFixed(2) + '%');
        }, { target: el, offset: ['start end', 'end start'] });
      } catch (e) { /* no parallax is strictly better than a broken one */ }
    });
  }

  /* ── 6. Entrance choreography ────────────────────────────────────
     Elements marked [data-ipm-enter] animate in on load with a real
     spring, staggered within their group. This is the "premium" beat —
     the page assembles itself instead of appearing.

     CSS holds [data-ipm-enter] at opacity 0 until it gains
     [data-ipm-played], so the un-animated first frame is never visible.
     That makes marking them played unconditional: if Motion is missing
     or motion is reduced, we still stamp the attribute, which reveals
     them instantly. Forgetting that would leave the page blank. */
  function playEntrance(M, scope) {
    var els = (scope || doc).querySelectorAll('[data-ipm-enter]:not([data-ipm-played])');
    if (!els.length) return;

    Array.prototype.forEach.call(els, function (el, i) {
      el.setAttribute('data-ipm-played', '');
      if (!M || REDUCED) return;

      var kind  = el.getAttribute('data-ipm-enter') || 'up';
      var from  = { opacity: [0, 1] };
      if (kind === 'up')    from.y     = [26, 0];
      if (kind === 'down')  from.y     = [-22, 0];
      if (kind === 'left')  from.x     = [-30, 0];
      if (kind === 'right') from.x     = [30, 0];
      if (kind === 'scale') from.scale = [0.92, 1];

      try {
        M.animate(el, from, Object.assign({}, SPRING_GENTLE, {
          delay: Math.min(i * STAGGER_STEP, STAGGER_CAP)
        }));
      } catch (e) {
        el.style.opacity = '1';
      }
    });
  }

  /* ── Wire-up ─────────────────────────────────────────────────────── */
  function scan(M, scope) {
    try { initReveal(scope); } catch (e) {}
    try { initFloat(scope); } catch (e) {}
    try {
      if (!REDUCED) {
        var mags = (scope || doc).querySelectorAll('[data-magnetic]');
        Array.prototype.forEach.call(mags, bindMagnetic);
        var tilts = (scope || doc).querySelectorAll('[data-tilt]');
        Array.prototype.forEach.call(tilts, bindTilt);
      }
    } catch (e) {}
    try { initParallax(M, scope); } catch (e) {}
    try { playEntrance(M, scope); } catch (e) {}
  }

  api.refresh = function (scope) {
    api.ready.then(function (M) { scan(M, scope); });
  };

  function boot() {
    /* Only now do we let the CSS hide anything — if this line never runs,
       every [data-ipm-reveal] element stays at its natural opacity. */
    root.classList.add('ipm-on');
    api.ready.then(function (M) { scan(M, doc); });
  }

  if (doc.readyState !== 'loading') boot();
  else doc.addEventListener('DOMContentLoaded', boot);
})();
