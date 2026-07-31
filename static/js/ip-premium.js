/* ═══════════════════════════════════════════════════════════════════
   ip-premium.js — the small amount of JS the finishing layer needs.

   Three jobs, none of which CSS can do alone:

     1. feed the cursor position to [data-spotlight] cards
     2. flag the document as scrolled so the sticky header can condense
     3. drive the reading-progress bar in browsers without a scroll timeline

   Everything is opt-in from markup and degrades to nothing. No page
   breaks if this file fails to load.
   ══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var root = document.documentElement;

  /* Honour both the OS setting and the in-app Settings toggle. The site
     writes its own preference to <html data-a11y-motion="reduce">, and a
     user who set it there did not necessarily set it in their OS. */
  function reducedMotion() {
    return (
      root.getAttribute('data-a11y-motion') === 'reduce' ||
      root.classList.contains('a11y-reduce-motion') ||
      (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
    );
  }

  var finePointer =
    window.matchMedia && window.matchMedia('(pointer: fine)').matches;

  /* ─────────────────────────────────────────────
     1. SPOTLIGHT

     One delegated pointermove on the document rather than a listener per
     card: a dashboard can hold thirty cards, and thirty listeners all
     firing on the same event is thirty times the work for one cursor.
     Writes are batched into a single rAF so a burst of pointermove
     events produces one style write per frame.
     ───────────────────────────────────────────── */
  (function spotlight() {
    if (!finePointer) return;

    /* The card families that already read as elevated surfaces. Stamping
       the attribute here rather than editing markup in 86 templates keeps
       [data-spotlight] as the single contract the CSS knows about, and
       lets a page opt out by simply not using these classes. */
    var AUTO = [
      '.feature-card', '.comparison-card', '.about-card', '.integration-card',
      '.step-card', '.review-card', '.lp-tool-card', '.answer-card',
      '.ipd-card', '.stat-card', '.preview-card', '.summary-card',
      '.pricing-card'
    ].join(',');

    function stamp() {
      var nodes = document.querySelectorAll(AUTO);
      for (var i = 0; i < nodes.length; i++) {
        if (!nodes[i].hasAttribute('data-spotlight')) {
          nodes[i].setAttribute('data-spotlight', '');
        }
      }
    }
    if (document.body) stamp();
    else document.addEventListener('DOMContentLoaded', stamp);

    var pending = null;
    var frame = 0;

    function flush() {
      frame = 0;
      if (!pending) return;
      var el = pending.el;
      var rect = el.getBoundingClientRect();
      if (!rect.width || !rect.height) { pending = null; return; }
      el.style.setProperty('--sx', ((pending.x - rect.left) / rect.width) * 100 + '%');
      el.style.setProperty('--sy', ((pending.y - rect.top) / rect.height) * 100 + '%');
      pending = null;
    }

    document.addEventListener(
      'pointermove',
      function (e) {
        if (e.pointerType !== 'mouse') return;
        var el = e.target.closest ? e.target.closest('[data-spotlight]') : null;
        if (!el) return;
        pending = { el: el, x: e.clientX, y: e.clientY };
        if (!frame) frame = requestAnimationFrame(flush);
      },
      { passive: true }
    );
  })();

  /* ─────────────────────────────────────────────
     2. SCROLLED FLAG

     An IntersectionObserver on a 1px sentinel instead of a scroll
     handler. The observer fires twice per page — once when the sentinel
     leaves the viewport and once when it comes back — where a scroll
     listener fires on every frame of every scroll for the same result.
     ───────────────────────────────────────────── */
  (function scrolledFlag() {
    var sentinel = document.createElement('div');
    sentinel.className = 'ip-scroll-sentinel';
    sentinel.setAttribute('aria-hidden', 'true');

    function attach() {
      if (!document.body) return;
      document.body.insertBefore(sentinel, document.body.firstChild);

      if (!('IntersectionObserver' in window)) {
        root.classList.add('is-scrolled');
        return;
      }
      new IntersectionObserver(
        function (entries) {
          root.classList.toggle('is-scrolled', !entries[0].isIntersecting);
        },
        { threshold: 0 }
      ).observe(sentinel);
    }

    if (document.body) attach();
    else document.addEventListener('DOMContentLoaded', attach);
  })();

  /* ─────────────────────────────────────────────
     3. READING PROGRESS FALLBACK

     Chrome and Safari drive this bar from a scroll timeline entirely on
     the compositor, so this code never runs there. Everywhere else it
     writes one custom property per animation frame, and only while the
     page is actually scrolling.
     ───────────────────────────────────────────── */
  (function readingProgress() {
    var supportsTimeline =
      window.CSS &&
      CSS.supports &&
      CSS.supports('animation-timeline', 'scroll()');
    if (supportsTimeline) return;

    function start() {
      if (!document.body || !document.body.hasAttribute('data-reading-progress')) return;
      var bar = document.querySelector('.ip-progress');
      if (!bar) return;

      var frame = 0;

      function update() {
        frame = 0;
        var doc = document.documentElement;
        var max = doc.scrollHeight - doc.clientHeight;
        var ratio = max > 0 ? doc.scrollTop / max : 0;
        bar.style.setProperty('--ip-progress', ratio.toFixed(4));
      }

      window.addEventListener(
        'scroll',
        function () {
          if (!frame) frame = requestAnimationFrame(update);
        },
        { passive: true }
      );
      window.addEventListener('resize', update, { passive: true });
      update();
    }

    if (document.body) start();
    else document.addEventListener('DOMContentLoaded', start);
  })();

  /* ─────────────────────────────────────────────
     4. PASSWORD REVEAL

     Injected rather than written into markup so login, register and any
     future password field get the same control without three templates
     having to agree on it. Wrapping moves the input inside a span that
     stays within the same form, so submission and password managers are
     unaffected.
     ───────────────────────────────────────────── */
  (function passwordReveal() {
    var EYE =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z"/>' +
      '<circle cx="12" cy="12" r="2.75"/></svg>';
    var EYE_OFF =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M3 3l18 18"/>' +
      '<path d="M10.6 6.1A9.6 9.6 0 0 1 12 5.5c6.4 0 10 6.5 10 6.5a17 17 0 0 1-3.4 4"/>' +
      '<path d="M6.4 8A17 17 0 0 0 2 12s3.6 6.5 10 6.5a9.9 9.9 0 0 0 3.7-.7"/>' +
      '<path d="M9.9 9.9a2.75 2.75 0 0 0 3.8 3.8"/></svg>';

    function attach() {
      var fields = document.querySelectorAll('input[type="password"].form-input');
      for (var i = 0; i < fields.length; i++) {
        var input = fields[i];
        if (input.parentNode && input.parentNode.classList.contains('pw-field')) continue;

        var wrap = document.createElement('span');
        wrap.className = 'pw-field';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'pw-toggle';
        btn.innerHTML = EYE;
        // aria-label rather than aria-pressed: the control's job is
        // named by what it will do next, and a toggle whose label never
        // changes forces a screen-reader user to guess the current state.
        btn.setAttribute('aria-label', 'Show password');
        btn.addEventListener('click', toggle);
        wrap.appendChild(btn);
      }
    }

    function toggle(e) {
      var btn = e.currentTarget;
      var input = btn.parentNode.querySelector('input');
      if (!input) return;
      var reveal = input.type === 'password';
      input.type = reveal ? 'text' : 'password';
      btn.innerHTML = reveal ? EYE_OFF : EYE;
      btn.setAttribute('aria-label', reveal ? 'Hide password' : 'Show password');
      // Clicking the button takes focus off the field; put it back where
      // the reader was, at the end of what they had typed.
      var end = input.value.length;
      input.focus();
      try { input.setSelectionRange(end, end); } catch (err) { /* type=email etc. */ }
    }

    if (document.body) attach();
    else document.addEventListener('DOMContentLoaded', attach);
  })();

  /* ─────────────────────────────────────────────
     5. VIEW TRANSITION GUARD

     A cross-document transition holds the outgoing frame until the new
     document is ready to paint. On a slow route that reads as a frozen
     page, so opt the navigation out once it has taken too long — the
     browser then does a plain load, which is what it would have done
     before this layer existed.
     ───────────────────────────────────────────── */
  (function viewTransitionGuard() {
    if (!('onpagereveal' in window) || !window.navigation) return;

    window.addEventListener('pageswap', function (e) {
      if (!e.viewTransition) return;
      if (reducedMotion()) { e.viewTransition.skipTransition(); return; }
      setTimeout(function () {
        try { e.viewTransition.skipTransition(); } catch (err) { /* already done */ }
      }, 600);
    });
  })();
})();
