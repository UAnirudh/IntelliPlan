/* ═══════════════════════════════════════════════════════════════════
   ip-motion.js — driver for ip-motion.css.

   Why an animation library and not hand-rolled rAF: the reveal and
   stagger work is trivial with CSS transitions, but the entrances (hero,
   auth panel, chat open) need settle behaviour that stops them looking
   like eased tweens, and scroll-linked progress needs a scroll driver
   that doesn't fight the main thread.

   The engine is GSAP, vendored under static/js/vendor/gsap/ rather than
   pulled from a CDN so it stays inside `script-src 'self'` (see the CSP
   block in App.py) and survives a CDN outage. This file used to run on
   Motion One; the logic below never touched Motion's API directly, only
   a five-function surface, so the swap is confined to `buildAdapter()`
   and shipping two animation engines was avoidable.

   Contract with the rest of the app — unchanged by the swap:
     window.IPMotion.animate / spring / stagger / inView / scroll
     window.IPMotion.ready      — Promise, resolves once the engine is up
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
    version: 5,
    engine: 'gsap',
    reduced: REDUCED,
    ready: null,
    springs: { gentle: SPRING_GENTLE, bouncy: SPRING_BOUNCY, snappy: SPRING_SNAPPY }
  };
  window.IPMotion = api;

  /* ── Spring → tween conversion ───────────────────────────────────
     GSAP tweens on a duration and an easing curve; it has no mass-spring
     integrator. Rather than eyeball a replacement for each of the three
     springs above, solve the second-order system the constants describe
     and pick a curve that lands in the same place.

       ω₀ = √(k/m)          undamped natural frequency
       ζ  = c / 2√(km)      damping ratio

     ζ ≥ 1 is critically or over-damped — it settles without overshoot,
     which is what an ease-out curve already does. ζ < 1 overshoots, and
     the amount it overshoots is what `back.out(overshoot)` takes. The
     settle time of a spring is ≈ 4 / (ζω₀); a little headroom on top of
     that keeps the tail of the curve from being visibly clipped. */
  function springToTween(opts) {
    var k = opts.stiffness || 170;
    var c = opts.damping   || 26;
    var m = opts.mass      || 1;
    var w0 = Math.sqrt(k / m);
    var zeta = c / (2 * Math.sqrt(k * m));
    var settle = 4 / (Math.max(zeta, 0.2) * w0);
    var duration = Math.min(1.2, Math.max(0.18, settle * 1.15));
    var ease;
    if (zeta >= 0.95) {
      ease = 'power2.out';
    } else {
      /* Peak overshoot of a step response is exp(−πζ/√(1−ζ²)). back.out's
         argument is roughly that overshoot expressed as a multiplier. */
      var overshoot = Math.exp(-Math.PI * zeta / Math.sqrt(1 - zeta * zeta));
      ease = 'back.out(' + (1 + overshoot * 4).toFixed(2) + ')';
    }
    return { duration: duration, ease: ease };
  }

  /* ── Adapter ──────────────────────────────────────────────────────
     Everything below this file's loader is written against five
     functions. Implementing those five on GSAP is the whole migration;
     the ~600 lines of behaviour underneath did not have to change.

     The signatures are Motion One's, deliberately: they are the shape the
     rest of this file already speaks, and `[from, to]` keyframe arrays
     map cleanly onto gsap.fromTo. */
  function buildAdapter(g) {
    var ST = window.ScrollTrigger;
    if (ST && g.registerPlugin) {
      try { g.registerPlugin(ST); } catch (e) {}
    }
    if (window.CustomEase && g.registerPlugin) {
      try { g.registerPlugin(window.CustomEase); } catch (e) {}
    }

    function toTweenVars(opts) {
      opts = opts || {};
      var vars = (opts.type === 'spring') ? springToTween(opts)
                                          : { duration: opts.duration || 0.4,
                                              ease: opts.ease || 'power2.out' };
      if (opts.delay) vars.delay = opts.delay;
      if (opts.repeat) vars.repeat = opts.repeat;
      if (opts.onComplete) vars.onComplete = opts.onComplete;
      return vars;
    }

    var M = {};

    M.animate = function (target, keyframes, opts) {
      var vars = toTweenVars(opts);
      var from = null, to = {};
      Object.keys(keyframes || {}).forEach(function (prop) {
        var v = keyframes[prop];
        if (Array.isArray(v)) {
          (from = from || {})[prop] = v[0];
          to[prop] = v[v.length - 1];
        } else {
          to[prop] = v;
        }
      });
      Object.keys(vars).forEach(function (kk) { to[kk] = vars[kk]; });
      var tween = from ? g.fromTo(target, from, to) : g.to(target, to);
      /* Motion One returns something with `.finished` and `.stop()`; keep
         both so no call site has to know which engine it got. */
      tween.finished = new Promise(function (res) {
        var prev = to.onComplete;
        tween.eventCallback('onComplete', function () {
          if (prev) { try { prev(); } catch (e) {} }
          res();
        });
      });
      tween.stop = function () { tween.kill(); };
      return tween;
    };

    M.inView = function (target, onEnter, opts) {
      if (!ST) return function () {};
      var t = ST.create({
        trigger: target,
        start: (opts && opts.start) || 'top 92%',
        once: true,
        onEnter: function () { try { onEnter({ target: target }); } catch (e) {} }
      });
      return function () { t.kill(); };
    };

    /* Motion One's scroll(cb, {target, offset}) reports 0→1 progress
       across the target's pass through the viewport. ScrollTrigger says
       the same thing with start/end and scrub. */
    M.scroll = function (cb, opts) {
      if (!ST) return function () {};
      opts = opts || {};
      var t = ST.create({
        trigger: opts.target || doc.body,
        start: 'top bottom',
        end: 'bottom top',
        scrub: true,
        onUpdate: function (self) { try { cb(self.progress); } catch (e) {} }
      });
      return function () { t.kill(); };
    };

    M.stagger = function (step) {
      return function (i) { return i * (step || STAGGER_STEP); };
    };

    M.spring = function (opts) {
      return Object.assign({ type: 'spring' }, opts || {});
    };

    api.animate = M.animate;
    api.inView  = M.inView;
    api.scroll  = M.scroll;
    api.stagger = M.stagger;
    api.spring  = M.spring;
    api.gsap    = g;
    return M;
  }

  /* ── Engine loader ────────────────────────────────────────────────
     The vendored builds are UMD, so they attach window.gsap and
     window.ScrollTrigger. Loading them with `defer` off normal <script>
     tags would be simpler, but this file is itself deferred and page
     scripts may call IPMotion immediately — hence a promise other code
     can await. */
  api.ready = new Promise(function (resolve) {
    if (window.gsap) { resolve(window.gsap); return; }
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
      resolve(window.gsap || null);
    }
    existing.addEventListener('load', done);
    existing.addEventListener('error', done);
    window.setTimeout(done, 2500);
  }).then(function (g) {
    if (!g) return null;
    try { return buildAdapter(g); } catch (e) { return null; }
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

  /* ── 8. Pro-tier patterns, rebuilt ───────────────────────────────
     Skiper's Pro components are licence-gated, so none of their source was
     available. These are original implementations of the same well-known
     interactions, written from the public demo descriptions. CSS lives in
     ip-motion.css §7b.
  */

  /* Scroll progress rail — pattern: "Anime js scrollbar" (skiper1). */
  function initScrollRail() {
    if (REDUCED || doc.getElementById('ipmScrollRail')) return;
    /* Pointless on a page that does not scroll, and a full-width bar at 0%
       just reads as a stray line. */
    if (doc.documentElement.scrollHeight <= window.innerHeight + 40) return;

    var rail = doc.createElement('div');
    rail.id = 'ipmScrollRail';
    rail.setAttribute('aria-hidden', 'true');
    doc.body.appendChild(rail);

    var frame = 0;
    function update() {
      frame = 0;
      var d = doc.documentElement;
      var max = d.scrollHeight - window.innerHeight;
      var p = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
      rail.style.setProperty('--ipm-scroll', p.toFixed(4));
      rail.classList.toggle('is-on', p > 0.005);
    }
    function onScroll() { if (!frame) frame = requestAnimationFrame(update); }
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    update();
  }

  /* Bouncy accordion — pattern: "Bouncy accordion" (skiper103).

     <details> is the right element and already works without JS, so this
     only adds the animation. The wrinkle: the UA hides non-summary content
     whenever [open] is absent, so a closing animation has nothing left to
     animate. Closing therefore runs first and the attribute is removed at
     the end, which means the click has to be intercepted. */
  function initAccordions(scope) {
    if (REDUCED) return;
    var els = (scope || doc).querySelectorAll('details:not([data-ipm-acc])');
    Array.prototype.forEach.call(els, function (d) {
      var summary = d.querySelector('summary');
      if (!summary || d.closest('[data-no-motion]')) return;
      d.setAttribute('data-ipm-acc', '');

      /* Wrap everything that is not the summary, so there is a single
         element to run grid-template-rows against. */
      var body = doc.createElement('div');
      body.className = 'ipm-acc__body';
      var inner = doc.createElement('div');
      body.appendChild(inner);
      var node = summary.nextSibling;
      while (node) {
        var next = node.nextSibling;
        inner.appendChild(node);
        node = next;
      }
      d.appendChild(body);
      d.classList.add('ipm-acc');

      summary.addEventListener('click', function (e) {
        e.preventDefault();
        if (d.open) {
          /* Closing: collapse first, drop [open] once the grid has settled.
             A guard timer backs the transitionend up — if the event never
             arrives (interrupted transition, a browser that skips it, the
             panel hidden mid-animation) the panel would be stuck open with
             no way to close it, which is far worse than an unanimated
             close. Both paths funnel through one idempotent finish(). */
          if (d.__ipmClosing) return;
          d.__ipmClosing = true;
          var timer = 0;
          var finish = function () {
            if (!d.__ipmClosing) return;
            d.__ipmClosing = false;
            window.clearTimeout(timer);
            body.removeEventListener('transitionend', onEnd);
            d.open = false;
            d.classList.remove('is-closing');
          };
          var onEnd = function (ev) {
            if (ev.target !== body || ev.propertyName !== 'grid-template-rows') return;
            finish();
          };
          body.addEventListener('transitionend', onEnd);
          timer = window.setTimeout(finish, 600);
          d.classList.add('is-closing');
        } else {
          d.classList.remove('is-closing');
          d.__ipmClosing = false;
          d.open = true;
        }
      });
    });
  }

  /* Auto-scale input — pattern: "Auto Scale input" (skiper105).
     The app already resizes these textareas by setting style.height; this
     only marks the moment the height changes so the CSS can settle it. */
  function initAutoScale(scope) {
    var els = (scope || doc).querySelectorAll('textarea:not([data-ipm-scale])');
    Array.prototype.forEach.call(els, function (t) {
      if (t.closest('[data-no-motion]')) return;
      t.setAttribute('data-ipm-scale', '');
      t.classList.add('ipm-autoscale');
      var last = t.offsetHeight;
      t.addEventListener('input', function () {
        /* Read after the app's own resize handler has run. */
        window.requestAnimationFrame(function () {
          var h = t.offsetHeight;
          if (h === last) return;
          last = h;
          if (REDUCED) return;
          t.classList.remove('ipm-grew');
          void t.offsetWidth;              // restart the animation
          t.classList.add('ipm-grew');
        });
      });
    });
  }

  /* Command search rail — pattern: "Vercel Command Search" (skiper92).
     One highlight that slides between rows rather than each row painting
     its own. Watches the active row via class mutations, because the
     palette's own keyboard handler is what moves it. */
  function initCmdRail() {
    var results = doc.querySelector('#cmdPalette .cmd-results');
    if (!results || results.hasAttribute('data-ipm-rail')) return;
    results.setAttribute('data-ipm-rail', '');

    var rail = doc.createElement('div');
    rail.className = 'ipm-cmd-rail';
    rail.setAttribute('aria-hidden', 'true');

    /* The palette rebuilds its results by assigning innerHTML on every
       keystroke, which throws the rail away with them. Re-attach on demand
       rather than only once at setup, or the highlight silently disappears
       the first time anyone types. */
    function ensureRail() {
      if (rail.parentNode !== results) {
        results.insertBefore(rail, results.firstChild);
        lastActive = null;               // forget stale geometry
      }
    }
    ensureRail();

    /* The rail lives inside the node being observed, and place() changes
       the rail's own class — so a naive observer callback re-triggers
       itself forever and locks the renderer. Two guards, both needed:
       ignore records whose target is the rail, and only touch the DOM when
       the active row has actually changed. */
    var lastActive = null;

    function place() {
      ensureRail();
      var active = results.querySelector('.cmd-item.active');
      if (active === lastActive) return;
      lastActive = active;
      if (!active) {
        if (rail.classList.contains('is-on')) rail.classList.remove('is-on');
        return;
      }
      rail.style.setProperty('--ipm-rail-y', active.offsetTop + 'px');
      rail.style.setProperty('--ipm-rail-h', active.offsetHeight + 'px');
      if (!rail.classList.contains('is-on')) rail.classList.add('is-on');
    }

    if (window.MutationObserver) {
      new MutationObserver(function (records) {
        for (var i = 0; i < records.length; i++) {
          if (records[i].target !== rail) { place(); return; }
        }
      }).observe(results, {
        subtree: true, childList: true,
        attributes: true, attributeFilter: ['class']
      });
    }
    results.addEventListener('mousemove', place);
    place();
  }

  /* ── Wire-up ─────────────────────────────────────────────────────── */
  function scan(M, scope) {
    try { initScrollRail(); } catch (e) {}
    try { initAccordions(scope); } catch (e) {}
    try { initAutoScale(scope); } catch (e) {}
    try { initCmdRail(); } catch (e) {}
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
