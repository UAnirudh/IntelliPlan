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
    /* Bumped whenever the behaviour of this file changes. Flask serves
       /static with long-lived caching, so this is the only way to tell a
       stale bundle from a live one when something does not work. */
    version: 2,
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
  var revealBackstop = 0;

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

    /* Backstop. This mechanism hides content until an observer callback
       says otherwise, so any environment where that callback does not
       arrive — a page that never composites, an aggressively throttled
       background tab, print, a headless renderer — would leave sections
       permanently blank. Nothing about the reveal is important enough to
       risk that, so anything still hidden after 4s is simply shown.
       Elements below the fold are unaffected: they have already been
       revealed by then only if they were actually scrolled into view, and
       revealing the rest early costs a nicety, not the page. */
    if (revealBackstop) window.clearTimeout(revealBackstop);
    revealBackstop = window.setTimeout(function () {
      var stuck = doc.querySelectorAll('[data-ipm-reveal]:not(.ipm-in)');
      Array.prototype.forEach.call(stuck, function (e) {
        e.classList.add('ipm-in', 'ipm-settled');
        if (revealObserver) revealObserver.unobserve(e);
      });
    }, 4000);
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

  /* ── 7. Sitewide auto-adoption ───────────────────────────────────
     The data-attributes above are opt-in, which meant only the handful of
     templates edited by hand had any motion. Enumerating the rest is not
     practical: this app has ~85 templates and the class names are
     page-local (hub-card, sch-card, oly-panel, bal-card, cc-card…), so a
     hardcoded allowlist would go stale immediately.

     So match on structure instead. A class ending in -card / -panel / -tile
     is a surface; a class ending in -grid / -list is a group. That covers
     every page including ones added later, and lives in one reviewable
     place rather than in 85 diffs.

     Anything inside [data-no-motion] is left alone, and a hand-authored
     attribute always wins — this only fills in what a template did not say.
  */
  /* Surfaces worth a hover lift. Deliberately includes -row and -item: on
     pages like /settings there is not a single "-card" on the page, it is
     built from settings-section / integration-row / sb-cust-item. Matching
     only card/panel/tile would have left most of the app untouched. */
  var SURFACE_RE = /(?:^|[\s])[\w]+-(?:card|panel|tile|row|item)(?:$|[\s])/;
  var GROUP_RE   = /(?:^|[\s])[\w]+-(?:grid|list|options|slots)(?:$|[\s])/;

  /* A lift is a hover affordance — it promises the thing responds. Putting
     it on a static block just makes the page twitch as the cursor crosses
     it, so only elements that actually do something get one. */
  function isInteractive(el) {
    var tag = el.tagName;
    if (tag === 'A' || tag === 'BUTTON' || tag === 'LABEL' || tag === 'SUMMARY') return true;
    if (el.hasAttribute('onclick') || el.hasAttribute('href')) return true;
    var role = el.getAttribute('role');
    if (role === 'button' || role === 'link' || role === 'option') return true;
    return !!el.querySelector('a[href], button, input, select, textarea, [role="button"]');
  }

  /* Rows and list items are small and dense; the full -6px card lift reads
     as the row jumping. They get a restrained version instead. */
  function isDenseRow(cls) {
    return /(?:^|[\s])[\w]+-(?:row|item)(?:$|[\s])/.test(cls);
  }

  /* Dense data views (a gradebook, a full scheduler) would turn into a
     shimmer of fading rows. Past this many candidates, skip the reveal and
     keep only the hover treatments. */
  var REVEAL_BUDGET = 48;

  function classOf(el) {
    var c = el.getAttribute && el.getAttribute('class');
    return typeof c === 'string' ? ' ' + c + ' ' : '';
  }

  /* Structural fallback for pages whose containers are not named *-grid or
     *-list — most of this app, as it turns out. A run of three or more
     siblings that all carry the same leading class is a repeated set
     whatever the parent happens to be called, and repeated sets are exactly
     what a stagger is for. */
  function looksLikeGroup(el) {
    var kids = el.children;
    if (kids.length < 3) return false;
    var first = (kids[0].getAttribute('class') || '').split(/\s+/)[0];
    if (!first) return false;
    var same = 0;
    for (var i = 0; i < kids.length; i++) {
      if ((kids[i].getAttribute('class') || '').split(/\s+/)[0] === first) same++;
    }
    return same === kids.length;
  }

  /* Reveal binds an IntersectionObserver against the viewport, so an element
     inside its own scroll box can sit "off screen" in that box while being
     on screen in the viewport, and never settle. Hover effects are fine
     there; reveals are not. */
  function inScrollBox(el) {
    for (var p = el.parentElement; p && p !== doc.body; p = p.parentElement) {
      var o = getComputedStyle(p).overflowY;
      if (o === 'auto' || o === 'scroll') return true;
    }
    return false;
  }

  function autoAdopt(scope) {
    var root = scope || doc;
    var main = root.querySelector ? (root.querySelector('main') || root) : root;
    if (!main || !main.querySelectorAll) return;

    var all = main.querySelectorAll('*');
    var surfaces = [], groups = [];

    Array.prototype.forEach.call(all, function (el) {
      if (el.closest('[data-no-motion]')) return;
      var cls = classOf(el);
      if (!cls) return;
      if (SURFACE_RE.test(cls)) surfaces.push(el);
      if (GROUP_RE.test(cls) || looksLikeGroup(el)) groups.push(el);
    });

    /* Hover lift on interactive surfaces. Pure hover — nothing is ever
       hidden — so this is safe to apply broadly. */
    surfaces.forEach(function (el) {
      if (el.hasAttribute('data-lift') || el.hasAttribute('data-lift-sm')) return;
      if (el.hasAttribute('data-tilt')) return;
      if (!isInteractive(el)) return;
      /* Skip anything that already animates its own transform on hover —
         two competing transforms on one element is a fight, not a design.
         Cheap check: does the element declare a transform transition? */
      var tr = getComputedStyle(el).transitionProperty || '';
      if (/transform|all/.test(tr)) return;
      el.setAttribute(isDenseRow(classOf(el)) ? 'data-lift-sm' : 'data-lift', '');
    });

    if (REDUCED) return;

    /* Staggered reveal for grid/list children, within budget. */
    var revealed = 0;
    groups.forEach(function (group) {
      if (revealed >= REVEAL_BUDGET) return;
      if (inScrollBox(group)) return;

      var kids = Array.prototype.filter.call(group.children, function (k) {
        if (k.hasAttribute('data-ipm-reveal') || !classOf(k)) return false;
        /* Only adopt what is still below the fold.

           html.ipm-on is already on the document by the time this runs, so
           tagging a visible element would drop it to opacity 0 and fade it
           back in — a flash on every page load. Off-screen elements have
           nothing to flash. This also means above-the-fold content paints
           immediately instead of waiting on an observer, which is the
           behaviour you want there anyway. */
        var r = k.getBoundingClientRect();
        return r.top > window.innerHeight;
      });
      /* One child is not a sequence, and a huge one is a data dump. */
      if (kids.length < 2 || kids.length > 12) return;
      if (revealed + kids.length > REVEAL_BUDGET) return;

      if (!group.hasAttribute('data-ipm-group')) group.setAttribute('data-ipm-group', '');
      kids.forEach(function (k) { k.setAttribute('data-ipm-reveal', 'up'); });
      revealed += kids.length;
    });

    /* Animated underline on prose links: inside main, real hrefs, not
       buttons, chips, tabs or anything already styled as a control. */
    var links = main.querySelectorAll('a[href]:not(.sk-link)');
    Array.prototype.forEach.call(links, function (a) {
      if (a.closest('[data-no-motion]')) return;
      var cls = classOf(a);
      if (/btn|chip|tab|pill|card|nav|logo|badge|icon/i.test(cls)) return;
      if (a.querySelector('img, svg')) return;          // icon links
      var text = (a.textContent || '').trim();
      if (!text || text.length > 60) return;
      a.classList.add('sk-link');
    });

    /* Magnetic pull on the page's primary call to action — the first one
       only. Every button leaning at the cursor is noise, not emphasis. */
    var cta = main.querySelector('.btn-primary, .cta-btn, .hero-btn');
    if (cta && !cta.hasAttribute('data-magnetic') && !cta.closest('[data-no-motion]')) {
      cta.setAttribute('data-magnetic', '0.2');
    }
  }

  /* ── Wire-up ─────────────────────────────────────────────────────── */
  function scan(M, scope) {
    try { autoAdopt(scope); } catch (e) {}
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

  /* ── Re-scan when content arrives ────────────────────────────────
     autoAdopt runs once at boot, which is too early for most of this app:
     the dashboard, gradebook, scheduler and friends render their content
     after a fetch, so a one-shot pass finds an empty <main> and enhances
     nothing. Watching for inserted nodes covers every async page without
     each template having to remember to call refresh().

     childList only, never attributes — autoAdopt's whole job is setting
     attributes, so observing those would feed itself forever. */
  function watch(M) {
    if (!window.MutationObserver || !doc.body) return;

    var pending = 0;
    var mo = new MutationObserver(function (records) {
      if (pending) return;
      /* Observe body, not <main>: several pages re-render by replacing the
         main element outright, which would leave an observer bound to <main>
         watching a detached node and silently never firing again.
         Body always survives, so the filter below does the scoping instead. */
      var main = doc.querySelector('main');
      var relevant = false;
      for (var i = 0; i < records.length && !relevant; i++) {
        var added = records[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          if (added[j].nodeType !== 1) continue;
          /* Ignore our own furniture — the tooltip re-appends itself to
             body on every hover, which would otherwise schedule a scan
             every time the cursor crosses a control. */
          if (added[j].id === 'ipTip') continue;
          if (!main || main.contains(added[j])) { relevant = true; break; }
        }
      }
      if (!relevant) return;

      /* Debounced: a render usually lands as a burst of insertions, and
         re-scanning per node would be quadratic on a long list. */
      pending = window.setTimeout(function () {
        pending = 0;
        try { scan(M, doc); } catch (e) {}
      }, 180);
    });
    mo.observe(doc.body, { childList: true, subtree: true });
  }

  function boot() {
    /* Only now do we let the CSS hide anything — if this line never runs,
       every [data-ipm-reveal] element stays at its natural opacity. */
    root.classList.add('ipm-on');
    api.ready.then(function (M) {
      scan(M, doc);
      try { watch(M); } catch (e) {}
    });
  }

  if (doc.readyState !== 'loading') boot();
  else doc.addEventListener('DOMContentLoaded', boot);
})();
