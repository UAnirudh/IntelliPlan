/* ═══════════════════════════════════════════════════════════════════
   ip-ui.js — behaviour for the components in ip-ui.css.

   Ported from the Watermelon shadcn registry, which ships React +
   Motion. This app has no build step, so each component is rebuilt as a
   progressive enhancement over markup that already works:

     · a stepper is an <input type="number"> before this file runs
     · a slider is an <input type="range">
     · a split button is a row of <button>s
     · a morphing button is a <form>

   That ordering is the point. If this file 404s, every one of them is
   still operable — plainer, but not broken. Nothing here creates a
   control from an empty <div>.

   Contract:
     IPUI.mount(scope)      upgrade everything in scope (idempotent)
     IPUI.get(el)           the instance bound to an element, if any
     IPUI.reduced           true when the OS asks for less motion

   Components announce changes on the element with a CustomEvent named
   `ipui:<component>` so page code can react without reaching inside.
   ══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var doc = document;

  var REDUCED = !!(window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  var api = {
    /* Bumped when behaviour changes — /static is served with long-lived
       caching, so this is how a stale bundle is told from a live one. */
    version: 1,
    reduced: REDUCED,
    components: {}
  };
  window.IPUI = api;

  function gsapNow() {
    return (window.IPGsap && window.IPGsap.gsap) || window.gsap || null;
  }

  function emit(el, name, detail) {
    try {
      el.dispatchEvent(new CustomEvent('ipui:' + name, {
        bubbles: true, detail: detail || {}
      }));
    } catch (e) {}
  }

  function attr(el, name, fallback) {
    var v = el.getAttribute(name);
    return v === null || v === '' ? fallback : v;
  }

  function numAttr(el, name, fallback) {
    var v = parseFloat(el.getAttribute(name));
    return isFinite(v) ? v : fallback;
  }

  /* ═══ Split button ═══════════════════════════════════════════════
     Markup:
       <div class="ipui-split" data-ipui="split">
         <button class="ipui-btn ipui-split__main">Add task</button>
         <div class="ipui-split__row">
           <button class="ipui-btn ipui-split__back" aria-label="Back">←</button>
           <button class="ipui-btn" data-value="assignment">Assignment</button>
           …
         </div>
       </div>

     The row is present and labelled from the start; opening only makes
     it visible. Choosing an option closes the row and emits
     `ipui:split` with the chosen value, so the page decides what a
     choice means.

     Focus is moved deliberately in both directions. Opening a set of
     choices and leaving focus on the button that just disappeared is the
     most common way this pattern gets built and the reason it is
     unusable without a mouse. */
  function mountSplit(root) {
    if (root.__ipui) return root.__ipui;

    var main = root.querySelector('.ipui-split__main');
    var row = root.querySelector('.ipui-split__row');
    var back = root.querySelector('.ipui-split__back');
    if (!main || !row) return null;

    var inst = { el: root, open: false };

    function setOpen(next) {
      if (inst.open === next) return;
      inst.open = next;
      root.classList.toggle('is-open', next);
      main.setAttribute('aria-expanded', next ? 'true' : 'false');
      /* inert would be tidier, but support is uneven enough that the
         visibility:hidden in the stylesheet is what actually keeps the
         hidden layer out of the tab order. This mirrors that for
         assistive tech that reads the tree rather than the paint. */
      row.setAttribute('aria-hidden', next ? 'false' : 'true');
      main.setAttribute('aria-hidden', next ? 'true' : 'false');

      var focusTarget = next
        ? (row.querySelector('[data-value]') || back)
        : main;
      if (focusTarget) {
        window.setTimeout(function () { focusTarget.focus(); },
          REDUCED ? 0 : 60);
      }
      emit(root, 'split', { open: next });
    }

    main.setAttribute('aria-expanded', 'false');
    row.setAttribute('aria-hidden', 'true');
    main.addEventListener('click', function () { setOpen(true); });
    if (back) back.addEventListener('click', function () { setOpen(false); });

    Array.prototype.forEach.call(row.querySelectorAll('[data-value]'), function (b) {
      b.addEventListener('click', function () {
        emit(root, 'split', { value: b.getAttribute('data-value'), button: b });
        setOpen(false);
      });
    });

    /* Escape closes, which is what everyone tries first. Bound on the
       container so it works from any of the options. */
    root.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && inst.open) {
        e.stopPropagation();
        setOpen(false);
      }
    });

    /* Clicking away is the other thing everyone tries. */
    doc.addEventListener('pointerdown', function (e) {
      if (inst.open && !root.contains(e.target)) setOpen(false);
    });

    inst.setOpen = setOpen;
    root.__ipui = inst;
    return inst;
  }

  /* ═══ Stepper ═════════════════════════════════════════════════════
     Markup:
       <div class="ipui-stepper" data-ipui="stepper" data-suffix="hrs">
         <button class="ipui-stepper__btn" data-dir="-1" aria-label="Decrease">−</button>
         <input class="ipui-stepper__input" type="number"
                min="1" max="8" step="1" value="2" name="hours">
         <span class="ipui-stepper__display" aria-hidden="true"></span>
         <span class="ipui-stepper__suffix">hrs</span>
         <button class="ipui-stepper__btn" data-dir="1" aria-label="Increase">+</button>
       </div>

     min/max/step/value all live on the input, because the input is the
     control — the buttons and the rolling display are an interface to
     it. Reading them from anywhere else would let the two disagree.

     Only digits that actually changed roll. Rolling all of them on every
     step makes 19 → 20 and 10 → 20 look identical, which throws away the
     information the animation exists to carry. */
  function mountStepper(root) {
    if (root.__ipui) return root.__ipui;

    var input = root.querySelector('.ipui-stepper__input');
    var display = root.querySelector('.ipui-stepper__display');
    if (!input) return null;

    var min = numAttr(input, 'min', 0);
    var max = numAttr(input, 'max', 999);
    var step = numAttr(input, 'step', 1);
    var decimals = (String(step).split('.')[1] || '').length;

    var inst = { el: root, input: input };
    var prevText = '';

    function clamp(v) {
      if (!isFinite(v)) v = min;
      return Math.min(max, Math.max(min, v));
    }

    function text() {
      return clamp(parseFloat(input.value)).toFixed(decimals);
    }

    function syncButtons() {
      var v = clamp(parseFloat(input.value));
      Array.prototype.forEach.call(root.querySelectorAll('[data-dir]'), function (b) {
        var dir = numAttr(b, 'data-dir', 1);
        b.disabled = dir < 0 ? v <= min : v >= max;
      });
    }

    /* Rebuild the display only where it differs. `dir` decides which way
       the old digit leaves and the new one arrives, so counting up and
       counting down are visibly different. */
    function render(dir) {
      if (!display) return;
      var next = text();
      var g = gsapNow();

      if (next.length !== prevText.length || !prevText || !g || REDUCED) {
        display.textContent = '';
        next.split('').forEach(function (ch) {
          var cell = doc.createElement('span');
          cell.className = 'ipui-stepper__digit';
          var glyph = doc.createElement('span');
          glyph.textContent = ch;
          cell.appendChild(glyph);
          display.appendChild(cell);
        });
        prevText = next;
        return;
      }

      var cells = display.querySelectorAll('.ipui-stepper__digit');
      next.split('').forEach(function (ch, i) {
        if (ch === prevText[i] || !cells[i]) return;
        var cell = cells[i];
        var outgoing = cell.firstElementChild;
        var incoming = doc.createElement('span');
        incoming.textContent = ch;
        cell.appendChild(incoming);

        var travel = dir >= 0 ? 20 : -20;
        g.fromTo(incoming,
          { y: travel, opacity: 0, scale: 0.5, filter: 'blur(2px)' },
          { y: 0, opacity: 1, scale: 1, filter: 'blur(0px)',
            duration: 0.42, ease: 'back.out(1.7)' });
        if (outgoing) {
          g.to(outgoing, {
            y: -travel, opacity: 0, scale: 0.5, filter: 'blur(2px)',
            duration: 0.34, ease: 'power2.in',
            onComplete: function () {
              if (outgoing.parentNode) outgoing.parentNode.removeChild(outgoing);
            }
          });
          /* The tween above is the only thing that removes the outgoing
             digit. If the ticker is not running — a backgrounded tab —
             digits would pile up inside the cell forever. */
          window.setTimeout(function () {
            if (outgoing.parentNode && outgoing !== cell.lastElementChild) {
              outgoing.parentNode.removeChild(outgoing);
            }
          }, 1200);
        }
      });
      prevText = next;
    }

    function commit(dir) {
      var before = clamp(parseFloat(input.value));
      var after = clamp(before + dir * step);
      if (after === before) return;
      input.value = after.toFixed(decimals);
      render(dir);
      syncButtons();
      /* `input` so any listener bound the normal way sees it, `change`
         because that is what form code listens for. */
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      emit(root, 'stepper', { value: after });
    }

    Array.prototype.forEach.call(root.querySelectorAll('[data-dir]'), function (b) {
      b.type = 'button';
      b.addEventListener('click', function () { commit(numAttr(b, 'data-dir', 1)); });
    });

    /* Typing into the input is still allowed, so the display has to
       follow it. Direction is inferred from which way the value moved. */
    input.addEventListener('input', function () {
      var was = parseFloat(prevText);
      var now = clamp(parseFloat(input.value));
      render(now >= was ? 1 : -1);
      syncButtons();
    });
    input.addEventListener('blur', function () {
      /* Snap an out-of-range or empty typed value back into range on the
         way out, rather than rejecting keystrokes as they are typed. */
      input.value = clamp(parseFloat(input.value)).toFixed(decimals);
      render(1);
      syncButtons();
    });

    var suffix = attr(root, 'data-suffix', '');
    if (suffix && !root.querySelector('.ipui-stepper__suffix')) {
      var s = doc.createElement('span');
      s.className = 'ipui-stepper__suffix';
      s.textContent = suffix;
      display.insertAdjacentElement('afterend', s);
    }

    render(1);
    syncButtons();

    inst.value = function (v) {
      if (v === undefined) return clamp(parseFloat(input.value));
      input.value = clamp(v).toFixed(decimals);
      render(1);
      syncButtons();
      return clamp(v);
    };
    root.__ipui = inst;
    return inst;
  }

  /* ═══ Save toggle ═════════════════════════════════════════════════
     Markup:
       <button class="ipui-save" data-ipui="save"
               data-idle="Save" data-saved="Saved">Save</button>

     States: idle → working → saved, and clicking a saved button returns
     it to idle.

     The button does not decide when the save finished. Page code calls
     inst.done() or inst.fail(msg), because the only honest source for
     "it saved" is the request coming back. The original component ran a
     fixed 1000ms timer and then claimed success unconditionally, which
     shows a check mark for a request that failed. */
  var CHECK_SVG =
    '<svg class="ipui-save__check" viewBox="0 0 20 20" fill="none" aria-hidden="true">' +
    '<path d="M4 10.5l4 4 8-9" stroke="currentColor" stroke-width="2.2" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var SPINNER_SVG =
    '<svg class="ipui-save__spinner" viewBox="0 0 26 26" aria-hidden="true">' +
    '<circle cx="13" cy="13" r="10" stroke-width="3" fill="none"/>' +
    '<path d="M13 3 A10 10 0 0 1 23 13" stroke-width="3" ' +
    'stroke-linecap="round" fill="none"/></svg>';

  function mountSave(btn) {
    if (btn.__ipui) return btn.__ipui;

    var idleText = attr(btn, 'data-idle', btn.textContent.trim() || 'Save');
    var savedText = attr(btn, 'data-saved', 'Saved');
    var inst = { el: btn, state: 'idle' };
    var spinTween = null;

    /* One live region for the whole control. Without it the state change
       is purely visual and a screen-reader user gets no confirmation
       that anything was saved. */
    var live = doc.createElement('span');
    live.className = 'ipui-sr';
    live.setAttribute('aria-live', 'polite');

    var label = doc.createElement('span');
    label.className = 'ipui-save__label';

    btn.textContent = '';
    btn.appendChild(label);
    btn.appendChild(live);
    if (!btn.getAttribute('type')) btn.type = 'button';

    function paint(state, message) {
      inst.state = state;
      btn.classList.toggle('is-working', state === 'working');
      btn.classList.toggle('is-saved', state === 'saved');
      btn.setAttribute('aria-busy', state === 'working' ? 'true' : 'false');

      if (spinTween) { spinTween.kill(); spinTween = null; }

      if (state === 'working') {
        label.innerHTML = SPINNER_SVG;
        var g = gsapNow();
        var svg = label.querySelector('svg');
        if (g && svg && !REDUCED) {
          spinTween = g.to(svg, {
            rotate: 360, duration: 0.7, ease: 'none',
            repeat: -1, transformOrigin: '50% 50%'
          });
        }
        live.textContent = 'Saving';
      } else if (state === 'saved') {
        label.innerHTML = CHECK_SVG + '<span>' + savedText + '</span>';
        live.textContent = message || savedText;
      } else {
        label.textContent = idleText;
        live.textContent = message || '';
      }
      emit(btn, 'save', { state: state });
    }

    btn.addEventListener('click', function () {
      if (inst.state === 'working') return;
      if (inst.state === 'saved') { paint('idle'); return; }
      paint('working');
      emit(btn, 'save-request', { instance: inst });
    });

    inst.start = function () { paint('working'); };
    inst.done = function (msg) { paint('saved', msg); };
    inst.reset = function () { paint('idle'); };
    inst.fail = function (msg) { paint('idle', msg || 'Could not save'); };

    paint('idle');
    btn.__ipui = inst;
    return inst;
  }

  /* ═══ Morphing button ═════════════════════════════════════════════
     Markup:
       <form class="ipui-morph" data-ipui="morph" action="…" method="post">
         <input class="ipui-morph__field" name="email" type="email"
                placeholder="Email" required>
         <button class="ipui-morph__btn" type="submit">
           <svg class="ipui-morph__icon">…</svg><span>Notify me</span>
         </button>
       </form>

     Collapsed, the button opens the field. Expanded, it submits. Because
     the wrapper is a real form, Enter submits and the browser validates
     `type=email` before any of this runs. */
  function mountMorph(form) {
    if (form.__ipui) return form.__ipui;

    var field = form.querySelector('.ipui-morph__field');
    var btn = form.querySelector('.ipui-morph__btn');
    if (!field || !btn) return null;

    var inst = { el: form, open: false };

    function setOpen(next) {
      if (inst.open === next) return;
      inst.open = next;
      form.classList.toggle('is-open', next);
      /* An input the user cannot see must not be focusable, or tabbing
         through the page lands in a zero-width box. */
      field.disabled = !next;
      if (next) window.setTimeout(function () { field.focus(); }, 120);
      emit(form, 'morph', { open: next });
    }

    field.disabled = true;

    form.addEventListener('submit', function (e) {
      if (!inst.open) {
        /* First press is "open", not "submit" — there is nothing typed
           yet to send. */
        e.preventDefault();
        setOpen(true);
        return;
      }
      if (!field.value.trim()) {
        e.preventDefault();
        field.focus();
        return;
      }
      /* Let the form submit normally unless the page took it over. */
      emit(form, 'morph-submit', { value: field.value.trim(), event: e });
    });

    form.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && inst.open) { setOpen(false); btn.focus(); }
    });

    doc.addEventListener('pointerdown', function (e) {
      if (inst.open && !form.contains(e.target) && !field.value.trim()) {
        setOpen(false);
      }
    });

    inst.setOpen = setOpen;
    inst.close = function () { field.value = ''; setOpen(false); };
    form.__ipui = inst;
    return inst;
  }

  /* ═══ Adaptive slider ═════════════════════════════════════════════
     Markup:
       <div class="ipui-slider" data-ipui="slider" data-unit="hrs/day">
         <div class="ipui-slider__readout">
           <span class="ipui-slider__value">2</span>
           <span class="ipui-slider__unit">hrs/day</span>
         </div>
         <div class="ipui-slider__track">
           <div class="ipui-slider__dots"><i></i>…</div>
           <div class="ipui-slider__fill"></div>
           <div class="ipui-slider__thumb"></div>
           <input class="ipui-slider__input" type="range"
                  min="0.5" max="8" step="0.5" value="2" name="hours">
         </div>
       </div>

     The tint ramps low → medium → high through the app's severity
     tokens, so "this is a lot" reads the same here as on a priority
     chip. */
  var TINTS = [
    { at: 0.00, tint: 'var(--low)',    fill: 'linear-gradient(90deg, color-mix(in srgb, var(--low) 45%, transparent), var(--low))' },
    { at: 0.55, tint: 'var(--medium)', fill: 'linear-gradient(90deg, color-mix(in srgb, var(--medium) 45%, transparent), var(--medium))' },
    { at: 0.80, tint: 'var(--high)',   fill: 'linear-gradient(90deg, color-mix(in srgb, var(--high) 45%, transparent), var(--high))' }
  ];

  function mountSlider(root) {
    if (root.__ipui) return root.__ipui;

    var input = root.querySelector('.ipui-slider__input');
    var fill = root.querySelector('.ipui-slider__fill');
    var thumb = root.querySelector('.ipui-slider__thumb');
    var value = root.querySelector('.ipui-slider__value');
    if (!input) return null;

    var inst = { el: root, input: input };
    var track = root.querySelector('.ipui-slider__track') || root;

    /* Geometry is measured on mount and on resize, never inside paint().
       paint() runs on every `input` event, which on a drag is every
       pointer move — reading offsetWidth there, straight after writing a
       custom property, forces a synchronous layout on each frame, which
       is precisely the cost the transform and clip-path exist to avoid. */
    var trackW = 0, thumbW = 0;

    function measure() {
      trackW = track.clientWidth || 0;
      thumbW = (thumb && thumb.offsetWidth) || 0;
    }

    function ratio() {
      var min = numAttr(input, 'min', 0);
      var max = numAttr(input, 'max', 100);
      var span = max - min;
      if (!span) return 0;
      return Math.min(1, Math.max(0, (parseFloat(input.value) - min) / span));
    }

    function paint() {
      var r = ratio();
      var band = TINTS[0];
      for (var i = 0; i < TINTS.length; i++) if (r >= TINTS[i].at) band = TINTS[i];

      root.style.setProperty('--ipui-slider-tint', band.tint);
      root.style.setProperty('--ipui-slider-fill', band.fill);

      /* Both offsets are in px, not percentages. A percentage inside
         translate() resolves against the element's own width — the thumb
         — not against the track, so the two are not interchangeable
         here. The travel is (track − thumb) so the thumb cannot overhang
         the end of the track at max, and the fill reaches the thumb's
         centre rather than its left edge. */
      var travel = Math.max(0, trackW - thumbW);
      var filled = r * travel + thumbW;

      if (fill) {
        fill.style.setProperty('--ipui-fill-inset',
          Math.max(0, trackW - filled).toFixed(1) + 'px');
      }
      if (thumb) {
        thumb.style.setProperty('--ipui-thumb-x', (r * travel).toFixed(1) + 'px');
      }
      if (value) value.textContent = input.value;
    }

    input.addEventListener('input', function () {
      paint();
      emit(root, 'slider', { value: parseFloat(input.value) });
    });
    input.addEventListener('change', function () {
      emit(root, 'slider-commit', { value: parseFloat(input.value) });
    });

    /* Dots are decoration; generating them here keeps the template from
       carrying six empty elements. */
    var dots = root.querySelector('.ipui-slider__dots');
    if (dots && !dots.children.length) {
      for (var i = 0; i < 6; i++) dots.appendChild(doc.createElement('i'));
    }

    window.addEventListener('resize', function () { measure(); paint(); });

    measure();
    paint();

    /* Mounting can happen before the track has been laid out — inside a
       collapsed <details>, which is where the manual scheduler's slider
       lives, or simply early enough that clientWidth is still 0. Both
       measure to zero, and a zero track makes the fill and the thumb sit
       at the left edge no matter the value.

       So re-measure unconditionally whenever the track's box changes, and
       once more on the next frame after mount. The observer callback only
       writes custom properties on the fill and the thumb, never anything
       that changes the track's own size, so it cannot feed itself. */
    if (window.ResizeObserver) {
      new ResizeObserver(function () { measure(); paint(); }).observe(track);
    } else {
      window.requestAnimationFrame(function () { measure(); paint(); });
    }

    inst.value = function (v) {
      if (v === undefined) return parseFloat(input.value);
      input.value = v;
      measure();
      paint();
      return v;
    };
    root.__ipui = inst;
    return inst;
  }

  /* ═══ Voice chat disclosure ═══════════════════════════════════════
     Markup:
       <div class="ipui-voice" data-ipui="voice" data-group="12"
            data-title="Voice — AP Calc BC"></div>

     Everything inside is built here, because unlike the other components
     there is nothing meaningful to render server-side: the roster is
     live, and a stale list of who *was* in a call is worse than no list.
     The element is empty until the first poll answers.

     Talks to intelliplan/api/group_voice.py. The contract that matters:
     `joined` in every response is the server's answer, not ours — if a
     heartbeat comes back joined:false we were swept out and the UI has
     to go back to showing the join button rather than pretending. */
  var FACE_TINTS = [
    'var(--accent)', 'var(--low)', 'var(--medium)', 'var(--high)',
    '#6d4aff', '#0d9488', '#c2410c', '#7c3aed'
  ];

  /* Same name always gets the same colour, for everyone in the room. A
     per-render random tint would repaint the roster every poll. */
  function tintFor(name) {
    var h = 0;
    var s = String(name || '?');
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return FACE_TINTS[h % FACE_TINTS.length];
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var EQ_BARS = '<span class="ipui-eq" aria-hidden="true"><i></i><i></i><i></i><i></i></span>';

  function faceHtml(seat, cls) {
    return '<span class="ipui-voice__face' + (cls ? ' ' + cls : '') +
      '" style="--ipui-face:' + tintFor(seat.name) + '" aria-hidden="true">' +
      escapeHtml(seat.initial) + '</span>';
  }

  function mountVoice(root) {
    if (root.__ipui) return root.__ipui;

    var groupId = root.getAttribute('data-group');
    if (!groupId) return null;

    var title = attr(root, 'data-title', 'Voice chat');
    var inst = { el: root, open: false, joined: false, seats: [], muted: true };
    var timer = 0;
    var heartbeatMs = 12000;

    function post(path, body) {
      return fetch('/api/groups/' + groupId + '/voice' + path, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {})
      }).then(function (r) { return r.json().catch(function () { return {}; }); });
    }

    function load() {
      return fetch('/api/groups/' + groupId + '/voice', { credentials: 'same-origin' })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(apply);
    }

    function apply(data) {
      if (!data || data.status === 'error') return data;
      inst.seats = data.seats || [];
      inst.joined = !!data.joined;
      inst.room = data.embed_url || null;
      if (data.heartbeat_seconds) heartbeatMs = data.heartbeat_seconds * 1000;
      var me = inst.seats.filter(function (s) { return s.is_you; })[0];
      if (me) inst.muted = !!me.muted;
      render();
      return data;
    }

    function peekHtml() {
      var n = inst.seats.length;
      if (!n) {
        return '<span class="ipui-voice__peek-text">Voice chat' +
          '<span class="ipui-voice__peek-sub">Nobody in here yet — be first</span></span>';
      }
      var shown = inst.seats.slice(0, 4);
      var extra = n - shown.length;
      return '<span class="ipui-voice__stack">' +
          shown.map(function (s) { return faceHtml(s); }).join('') +
        '</span>' +
        '<span class="ipui-voice__peek-text">' +
          (extra > 0 ? '+' + extra + ' more' : (n === 1 ? '1 person' : n + ' people')) +
          '<span class="ipui-voice__peek-sub">' +
            (inst.joined ? 'You are in voice' : 'in voice now') +
          '</span>' +
        '</span>';
    }

    function seatHtml(s) {
      /* The equaliser is the only thing distinguishing talking from
         listening, and it is decoration to a screen reader. The state
         goes in the name instead so it is available either way. */
      var state = s.muted ? 'muted' : 'unmuted';
      return '<div class="ipui-voice__seat">' +
        '<span class="ipui-voice__seat-face">' +
          faceHtml(s) +
          (s.muted ? '' : '<span class="ipui-voice__badge">' + EQ_BARS + '</span>') +
        '</span>' +
        '<span class="ipui-voice__seat-name">' + escapeHtml(s.name) +
          (s.is_you ? ' (you)' : '') +
          '<span class="ipui-sr">, ' + state + '</span>' +
        '</span>' +
      '</div>';
    }

    /* The shell is built once and then patched region by region.

       This is not an optimisation. The panel contains the Jitsi iframe,
       and re-assigning innerHTML tears that iframe out of the document
       and builds a new one — which drops the call, revokes the
       microphone permission, and rejoins the room. Doing that on a
       twelve-second poll would make the call unusable. Only the roster,
       the peek row, and the footer are ever rewritten; the stage is
       created when you join and removed when you leave, and is otherwise
       never touched. */
    var parts = null;

    function buildShell() {
      root.innerHTML =
        '<span class="ipui-voice__live" hidden>' + EQ_BARS + ' live</span>' +
        '<button class="ipui-voice__peek" type="button" aria-expanded="false"></button>' +
        '<div class="ipui-voice__panel">' +
          '<div class="ipui-voice__head">' +
            '<h3 class="ipui-voice__title">' + escapeHtml(title) + '</h3>' +
            '<button class="ipui-voice__close" type="button" aria-label="Close voice chat">✕</button>' +
          '</div>' +
          '<div class="ipui-voice__roster"></div>' +
          '<div class="ipui-voice__stage" hidden></div>' +
          '<div class="ipui-voice__foot">' +
            '<button class="ipui-btn ipui-voice__cta" type="button"></button>' +
            '<p class="ipui-voice__hint"></p>' +
          '</div>' +
        '</div>';

      parts = {
        live:   root.querySelector('.ipui-voice__live'),
        peek:   root.querySelector('.ipui-voice__peek'),
        roster: root.querySelector('.ipui-voice__roster'),
        stage:  root.querySelector('.ipui-voice__stage'),
        cta:    root.querySelector('.ipui-voice__cta'),
        hint:   root.querySelector('.ipui-voice__hint')
      };
    }

    function render() {
      if (!parts) buildShell();

      var anyLive = inst.seats.some(function (s) { return !s.muted; });

      parts.live.hidden = !inst.seats.length;
      parts.peek.innerHTML = peekHtml();
      parts.peek.setAttribute('aria-expanded', inst.open ? 'true' : 'false');

      parts.roster.innerHTML = inst.seats.length
        ? '<div class="ipui-voice__grid">' + inst.seats.map(seatHtml).join('') + '</div>'
        : '<p class="ipui-voice__empty">No one is in voice. Joining puts you in the room and lets the rest of the group see you are there.</p>';

      /* The iframe exists only while seated. An iframe that can prompt
         for a microphone has no business sitting on a page nobody joined
         from — and once it is there, it is left alone. */
      var wantStage = !!(inst.joined && inst.room);
      var haveStage = !!parts.stage.firstChild;
      if (wantStage && !haveStage) {
        var frame = doc.createElement('iframe');
        frame.title = 'Voice room';
        /* Microphone only. No camera, and no screen share. */
        frame.setAttribute('allow', 'microphone; autoplay');
        frame.src = inst.room;
        parts.stage.appendChild(frame);
        parts.stage.hidden = false;
      } else if (!wantStage && haveStage) {
        parts.stage.innerHTML = '';
        parts.stage.hidden = true;
      }

      parts.cta.textContent = inst.joined ? 'Leave voice' : 'Join voice';
      parts.cta.setAttribute('data-act', inst.joined ? 'leave' : 'join');
      parts.cta.classList.toggle('ipui-btn--primary', !inst.joined);
      parts.cta.disabled = false;
      parts.hint.textContent = inst.joined
        ? 'Mute and hang up from the controls in the room.'
        : 'You join muted. Nobody hears you until you unmute.';

      root.classList.toggle('is-open', inst.open);
      root.classList.toggle('is-live', anyLive);
    }

    function setOpen(next) {
      inst.open = next;
      render();
      emit(root, 'voice', { open: next, joined: inst.joined });
    }

    /* Poll only while it can matter: a closed panel that nobody has
       joined does not need a request every twelve seconds on every group
       page. Joined, we always poll — that heartbeat is what keeps the
       seat alive. */
    function scheduleNext() {
      window.clearTimeout(timer);
      var shouldPoll = inst.joined || inst.open;
      if (!shouldPoll) return;
      /* A hidden tab throttles timers anyway; skipping the request
         outright avoids a burst of queued fetches when it comes back. */
      timer = window.setTimeout(tick, doc.hidden ? heartbeatMs * 3 : heartbeatMs);
    }

    function tick() {
      var p = inst.joined
        ? post('/heartbeat', { muted: inst.muted })
        : load();
      p.then(apply).catch(function () {}).then(scheduleNext);
    }

    root.addEventListener('click', function (e) {
      if (e.target.closest('.ipui-voice__close')) { setOpen(false); scheduleNext(); return; }
      if (e.target.closest('.ipui-voice__peek')) { setOpen(true); tick(); return; }

      var act = e.target.closest('[data-act]');
      if (!act) return;
      var which = act.getAttribute('data-act');
      act.disabled = true;

      if (which === 'join') {
        post('/join').then(function (d) {
          if (d && d.status === 'error') {
            if (window.IP && IP.toast) IP.toast(d.message || 'Could not join.', 'error');
            act.disabled = false;
            return;
          }
          apply(d);
          scheduleNext();
          emit(root, 'voice-join', {});
        });
      } else if (which === 'leave') {
        post('/leave').then(function (d) { apply(d); scheduleNext(); });
      }
    });

    /* Best-effort exit. The server does not rely on this — a seat that
       stops reporting is dropped regardless — but sending it means the
       roster updates immediately for everyone else instead of after the
       stale window. `pagehide` rather than `unload`, which is not fired
       on mobile Safari when an app is swiped away. */
    window.addEventListener('pagehide', function () {
      if (!inst.joined) return;
      try {
        navigator.sendBeacon('/api/groups/' + groupId + '/voice/leave', new Blob([], {
          type: 'application/json'
        }));
      } catch (e) {}
    });

    /* Coming back to a backgrounded tab should show the truth right
       away, not at the next scheduled poll. */
    doc.addEventListener('visibilitychange', function () {
      if (!doc.hidden && (inst.joined || inst.open)) tick();
    });

    render();
    load().then(scheduleNext);

    inst.refresh = tick;
    root.__ipui = inst;
    return inst;
  }

  /* ═══ Date strip ══════════════════════════════════════════════════
     Markup:
       <div class="ipui-dates" data-ipui="dates" data-days="14"
            data-name="plan_date"></div>

     Built here rather than server-side: the range starts at *today*, and
     a server-rendered strip would be a day stale for anyone who left the
     tab open overnight — the most common way this page is used.

     inst.selected() returns ISO dates in ascending order. */
  function mountDates(root) {
    if (root.__ipui) return root.__ipui;

    var count = Math.min(60, Math.max(1, numAttr(root, 'data-days', 14)));
    var name = attr(root, 'data-name', 'plan_date');
    var inst = { el: root };

    /* Local midnight, not UTC. `new Date().toISOString()` rolls the date
       over for anyone west of Greenwich in the evening, which would show
       tomorrow's strip and stamp blocks onto the wrong day. */
    function isoLocal(d) {
      return d.getFullYear() + '-' +
        String(d.getMonth() + 1).padStart(2, '0') + '-' +
        String(d.getDate()).padStart(2, '0');
    }

    var today = new Date();
    today.setHours(0, 0, 0, 0);
    var todayIso = isoLocal(today);

    var html = '';
    for (var i = 0; i < count; i++) {
      var d = new Date(today);
      d.setDate(today.getDate() + i);
      var iso = isoLocal(d);
      html +=
        '<label class="ipui-dates__day' + (iso === todayIso ? ' is-today' : '') + '">' +
          '<input type="checkbox" name="' + name + '" value="' + iso + '">' +
          '<span class="ipui-dates__dow">' +
            d.toLocaleDateString(undefined, { weekday: 'short' }) + '</span>' +
          '<span class="ipui-dates__num">' + d.getDate() + '</span>' +
          '<span class="ipui-dates__mon">' +
            d.toLocaleDateString(undefined, { month: 'short' }) + '</span>' +
        '</label>';
    }
    root.innerHTML = html;
    root.setAttribute('role', 'group');

    root.addEventListener('change', function () {
      emit(root, 'dates', { selected: inst.selected() });
    });

    inst.selected = function () {
      return Array.prototype.map.call(
        root.querySelectorAll('input:checked'), function (i) { return i.value; }).sort();
    };
    inst.select = function (isoList) {
      var want = {};
      (isoList || []).forEach(function (d) { want[d] = true; });
      Array.prototype.forEach.call(root.querySelectorAll('input'), function (i) {
        i.checked = !!want[i.value];
      });
      emit(root, 'dates', { selected: inst.selected() });
    };
    inst.today = todayIso;

    root.__ipui = inst;
    return inst;
  }

  /* ═══ Slot picker ═════════════════════════════════════════════════
     Markup:
       <div class="ipui-slots" data-ipui="slots"></div>

     Rows are added by inst.add() or by the page's own "add block"
     control. inst.blocks() reads them back in the shape
     intelliplan/api/manual_schedule.py expects.

     The client checks overlap so the student sees it while typing, but
     the server checks it again and is the authority — the same endpoint
     is reachable without this page. */
  function mountSlots(root) {
    if (root.__ipui) return root.__ipui;

    var inst = { el: root };

    function rowHtml(b) {
      b = b || {};
      return '<div class="ipui-slot' + (b.is_break ? ' is-break' : '') + '">' +
        '<span class="ipui-slot__times">' +
          '<input class="ipui-slot__time" type="time" data-f="start" ' +
                 'aria-label="Start time" value="' + escapeHtml(b.start || '17:00') + '">' +
          '<span class="ipui-slot__dash" aria-hidden="true">–</span>' +
          '<input class="ipui-slot__time" type="time" data-f="end" ' +
                 'aria-label="End time" value="' + escapeHtml(b.end || '18:00') + '">' +
        '</span>' +
        '<span class="ipui-slot__body">' +
          '<input class="ipui-slot__title" data-f="assignment" aria-label="What are you working on" ' +
                 'placeholder="What are you working on?" maxlength="200" ' +
                 'value="' + escapeHtml(b.assignment || '') + '">' +
          '<input class="ipui-slot__course" data-f="course" aria-label="Course" ' +
                 'placeholder="Course (optional)" maxlength="120" ' +
                 'value="' + escapeHtml(b.course || '') + '">' +
        '</span>' +
        '<span class="ipui-slot__tools">' +
          '<button class="ipui-slot__tool" type="button" data-act="break" ' +
                  'aria-pressed="' + (b.is_break ? 'true' : 'false') + '" ' +
                  'title="Mark as a break" aria-label="Mark as a break">☕</button>' +
          '<button class="ipui-slot__tool is-remove" type="button" data-act="remove" ' +
                  'title="Remove this block" aria-label="Remove this block">✕</button>' +
        '</span>' +
        '<p class="ipui-slot__error" hidden></p>' +
      '</div>';
    }

    function rows() {
      return Array.prototype.slice.call(root.querySelectorAll('.ipui-slot'));
    }

    function read(row) {
      function f(name) {
        var el = row.querySelector('[data-f="' + name + '"]');
        return el ? el.value.trim() : '';
      }
      return {
        start: f('start'),
        end: f('end'),
        assignment: f('assignment'),
        course: f('course'),
        is_break: row.querySelector('[data-act="break"]')
          .getAttribute('aria-pressed') === 'true'
      };
    }

    function toMinutes(hhmm) {
      var m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || '');
      if (!m) return null;
      var h = +m[1], mm = +m[2];
      if (h > 23 || mm > 59) return null;
      return h * 60 + mm;
    }

    /* Validation that runs as they type. It mirrors the server's rules
       rather than replacing them — the point is that a student finds out
       about an overlap while looking at the row, not after pressing
       Build. */
    function validate() {
      var parsed = rows().map(function (row) {
        var b = read(row);
        return { row: row, b: b, s: toMinutes(b.start), e: toMinutes(b.end) };
      });

      parsed.forEach(function (p) {
        var msg = '';
        if (p.s === null || p.e === null) msg = 'Needs a start and an end time.';
        else if (p.e <= p.s) msg = 'Ends before it starts.';
        else if (!p.b.assignment && !p.b.is_break) msg = 'Give this block a name.';
        p.msg = msg;
      });

      var ordered = parsed.filter(function (p) { return !p.msg; })
                          .sort(function (a, b) { return a.s - b.s; });
      for (var i = 1; i < ordered.length; i++) {
        if (ordered[i].s < ordered[i - 1].e) {
          ordered[i].msg = 'Overlaps “' +
            (ordered[i - 1].b.assignment || 'the block before it') + '”.';
        }
      }

      parsed.forEach(function (p) {
        var err = p.row.querySelector('.ipui-slot__error');
        p.row.classList.toggle('is-invalid', !!p.msg);
        err.hidden = !p.msg;
        err.textContent = p.msg || '';
      });

      var ok = parsed.every(function (p) { return !p.msg; }) && parsed.length > 0;
      emit(root, 'slots', { valid: ok, count: parsed.length });
      return ok;
    }

    root.addEventListener('input', validate);
    root.addEventListener('click', function (e) {
      var act = e.target.closest('[data-act]');
      if (!act) return;
      var row = act.closest('.ipui-slot');
      if (act.getAttribute('data-act') === 'remove') {
        row.remove();
        if (!rows().length) inst.add();
        validate();
        return;
      }
      if (act.getAttribute('data-act') === 'break') {
        var on = act.getAttribute('aria-pressed') !== 'true';
        act.setAttribute('aria-pressed', on ? 'true' : 'false');
        row.classList.toggle('is-break', on);
        validate();
      }
    });

    inst.add = function (block) {
      /* Default the new row to start where the last one ended. Building a
         day is a sequence, and retyping the same time you just entered on
         the row above is the friction that makes people give up on manual
         schedulers. */
      if (!block) {
        var last = rows()[rows().length - 1];
        if (last) {
          var prev = read(last);
          var end = toMinutes(prev.end);
          if (end !== null) {
            var s = end, e = Math.min(23 * 60 + 59, end + 60);
            block = {
              start: String(Math.floor(s / 60)).padStart(2, '0') + ':' +
                     String(s % 60).padStart(2, '0'),
              end: String(Math.floor(e / 60)).padStart(2, '0') + ':' +
                   String(e % 60).padStart(2, '0')
            };
          }
        }
      }
      root.insertAdjacentHTML('beforeend', rowHtml(block));
      validate();
      var added = rows()[rows().length - 1];
      var title = added.querySelector('[data-f="assignment"]');
      if (title && !(block && block.assignment)) title.focus();
      return added;
    };

    inst.set = function (blocks) {
      root.innerHTML = (blocks || []).map(rowHtml).join('');
      if (!rows().length) inst.add({});
      validate();
    };

    inst.blocks = function () { return rows().map(read); };
    inst.valid = validate;

    if (!rows().length) inst.add({ assignment: '' });
    validate();

    root.__ipui = inst;
    return inst;
  }

  /* ═══ Timed undo ══════════════════════════════════════════════════
     Markup:
       <button class="ipui-undo" data-ipui="undo"
               data-seconds="6" data-label="Hide" data-armed="Undo">Hide</button>

     First press arms it and starts the countdown. A second press cancels.
     Only when the timer runs out does `ipui:undo-commit` fire — the page
     does the actual work there.

     That ordering is the whole component. Firing the action first and
     offering to reverse it would need every caller to implement an undo
     path; deferring it means the caller implements nothing, and an action
     that was cancelled simply never happened. It follows that this is
     only usable where a few seconds of delay is acceptable — which is
     true of hiding, dismissing, and archiving, and not of anything the
     student is waiting on a result from. */
  var UNDO_ICON =
    '<span class="ipui-undo__icon" aria-hidden="true">' +
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" ' +
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-4"/></svg></span>';

  function mountUndo(btn) {
    if (btn.__ipui) return btn.__ipui;

    var seconds = Math.max(2, numAttr(btn, 'data-seconds', 6));
    var idleLabel = attr(btn, 'data-label', btn.textContent.trim() || 'Delete');
    var armedLabel = attr(btn, 'data-armed', 'Undo');
    var inst = { el: btn, armed: false };
    var timer = 0, left = seconds;

    var label = doc.createElement('span');
    var count = doc.createElement('span');
    count.className = 'ipui-undo__count';

    var live = doc.createElement('span');
    live.className = 'ipui-sr';
    live.setAttribute('aria-live', 'assertive');

    btn.textContent = '';
    btn.insertAdjacentHTML('afterbegin', UNDO_ICON);
    btn.appendChild(label);
    btn.appendChild(count);
    btn.appendChild(live);
    if (!btn.getAttribute('type')) btn.type = 'button';

    function paint() {
      btn.classList.toggle('is-armed', inst.armed);
      label.textContent = inst.armed ? armedLabel : idleLabel;
      count.textContent = inst.armed ? left + 's' : '';
    }

    function stop() {
      window.clearInterval(timer);
      timer = 0;
      inst.armed = false;
      left = seconds;
      paint();
    }

    function tick() {
      left -= 1;
      if (left > 0) { paint(); return; }
      stop();
      live.textContent = idleLabel + ' done.';
      emit(btn, 'undo-commit', {});
    }

    btn.addEventListener('click', function () {
      if (inst.armed) {
        stop();
        live.textContent = 'Cancelled.';
        emit(btn, 'undo-cancel', {});
        return;
      }
      inst.armed = true;
      left = seconds;
      paint();
      /* Assertive, because the window to act on it closes. */
      live.textContent = idleLabel + ' in ' + seconds + ' seconds. Press again to undo.';
      emit(btn, 'undo-arm', { seconds: seconds });
      timer = window.setInterval(tick, 1000);
    });

    inst.cancel = stop;
    paint();
    btn.__ipui = inst;
    return inst;
  }

  /* ═══ Pin ═════════════════════════════════════════════════════════
     Markup:
       <div data-ipui="pin-list" data-pinned-label="Pinned">
         <div class="ipui-pinned-group" hidden>
           <p class="ipui-pinned-group__title">Pinned</p>
           <div class="ipui-pinned-group__items"></div>
         </div>
         <div data-pin-items>
           <div data-pin-id="7"> … <button class="ipui-pin" aria-pressed="false">…</button></div>
         </div>
       </div>

     Pinning moves the row into the group above. Flip is used because the
     row changes parent: animating position alone would show one row
     fading out while a different one fades in, which reads as a copy
     rather than as the same thing moving. Flip is fetched on demand — a
     page with no pinnable list never pays for it. */
  function mountPinList(root) {
    if (root.__ipui) return root.__ipui;

    var group = root.querySelector('.ipui-pinned-group');
    var groupItems = root.querySelector('.ipui-pinned-group__items');
    var rest = root.querySelector('[data-pin-items]');
    if (!group || !groupItems || !rest) return null;

    var inst = { el: root };

    function sync() {
      var pinned = groupItems.children.length;
      group.hidden = pinned === 0;
    }

    function move(row, pinned) {
      var g = gsapNow();
      var Flip = window.Flip;

      function apply() {
        (pinned ? groupItems : rest).appendChild(row);
        sync();
      }

      if (!g || !Flip || REDUCED) { apply(); return; }
      var state = Flip.getState(row);
      apply();
      Flip.from(state, { duration: 0.45, ease: 'power2.inOut' });
    }

    root.addEventListener('click', function (e) {
      var btn = e.target.closest('.ipui-pin');
      if (!btn) return;
      var row = btn.closest('[data-pin-id]');
      if (!row) return;

      var next = btn.getAttribute('aria-pressed') !== 'true';
      btn.setAttribute('aria-pressed', next ? 'true' : 'false');
      btn.setAttribute('aria-label', next ? 'Unpin' : 'Pin to the top');

      /* Move optimistically and let the page tell us if the save failed —
         a pin that waits on a round trip feels broken. */
      move(row, next);
      emit(root, 'pin', {
        id: row.getAttribute('data-pin-id'),
        pinned: next,
        row: row,
        revert: function () {
          btn.setAttribute('aria-pressed', next ? 'false' : 'true');
          move(row, !next);
        }
      });
    });

    /* Preload Flip on first hover, so the first pin animates rather than
       snapping while the plugin is still in flight. */
    root.addEventListener('pointerenter', function () {
      if (window.IPGsap && !REDUCED) window.IPGsap.plugin('Flip');
    }, { once: true });

    sync();
    root.__ipui = inst;
    return inst;
  }

  /* ═══ Wiggling cards ══════════════════════════════════════════════
     Markup:
       <div class="ipui-wiggle" data-ipui="wiggle">
         <article class="ipui-wiggle__card">…</article>
         …
       </div>

     CSS owns the tilt, blur and fade; this only writes `--d`, each
     card's signed distance from the centre of the strip in card widths.
     Scroll-snap does the paging, so a trackpad, a swipe, and the arrow
     keys all work whether or not this runs.

     Reading geometry on every scroll event would thrash layout, so
     positions are measured once per resize and the scroll handler only
     does arithmetic. */
  function mountWiggle(root) {
    if (root.__ipui) return root.__ipui;

    var cards = Array.prototype.slice.call(root.querySelectorAll('.ipui-wiggle__card'));
    if (!cards.length) return null;

    var inst = { el: root, cards: cards };
    var centres = [];
    var frame = 0;
    var dots = null;

    function measure() {
      /* offsetLeft is relative to the scroll container's padding box,
         which is what scrollLeft is measured against — so the two can be
         subtracted without another getBoundingClientRect per card. */
      centres = cards.map(function (c) { return c.offsetLeft + c.offsetWidth / 2; });
      inst.width = cards[0].offsetWidth || 1;
    }

    function paint() {
      frame = 0;
      var mid = root.scrollLeft + root.clientWidth / 2;
      var nearest = 0, best = Infinity;
      for (var i = 0; i < cards.length; i++) {
        var d = (centres[i] - mid) / inst.width;
        cards[i].style.setProperty('--d', d.toFixed(3));
        var abs = Math.abs(d);
        if (abs < best) { best = abs; nearest = i; }
      }
      if (inst.index !== nearest) {
        inst.index = nearest;
        if (dots) {
          Array.prototype.forEach.call(dots.children, function (b, i) {
            b.setAttribute('aria-current', i === nearest ? 'true' : 'false');
          });
        }
        emit(root, 'wiggle', { index: nearest });
      }
    }

    function onScroll() { if (!frame) frame = requestAnimationFrame(paint); }

    root.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', function () { measure(); onScroll(); });

    /* Dots double as the keyboard affordance: the strip itself is
       focusable and arrow-scrollable, but a row of buttons is what makes
       "there are four of these" obvious. */
    if (root.hasAttribute('data-dots')) {
      dots = doc.createElement('div');
      dots.className = 'ipui-wiggle__dots';
      cards.forEach(function (c, i) {
        var b = doc.createElement('button');
        b.type = 'button';
        b.className = 'ipui-wiggle__dot';
        b.setAttribute('aria-label', 'Go to card ' + (i + 1));
        b.addEventListener('click', function () {
          c.scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth',
                             block: 'nearest', inline: 'center' });
        });
        dots.appendChild(b);
      });
      root.insertAdjacentElement('afterend', dots);
    }

    root.setAttribute('tabindex', '0');
    root.setAttribute('role', 'region');

    measure();
    paint();

    /* Web fonts land after this runs and change every card's width, so
       one re-measure once they are ready keeps the first paint honest. */
    if (doc.fonts && doc.fonts.ready) {
      doc.fonts.ready.then(function () { measure(); onScroll(); });
    }

    inst.refresh = function () { measure(); onScroll(); };
    root.__ipui = inst;
    return inst;
  }

  /* ═══ Announcement bar ════════════════════════════════════════════
     Markup:
       <div class="ipui-announce" data-ipui="announce" data-key="api-2026-08"
            hidden>…<button class="ipui-announce__close">…</button></div>

     `data-key` is the whole design. A bar that remembers only
     "dismissed" can never be reused: every later announcement is either
     invisible to returning visitors or has to un-dismiss itself for
     everyone, and that second one is how these turn into nagware. The
     key is per-announcement, so dismissing this one says nothing about
     the next.

     The element ships `hidden` and is revealed here. Rendering it and
     then hiding it would flash the bar on every load for someone who
     dismissed it a month ago. */
  function mountAnnounce(root) {
    if (root.__ipui) return root.__ipui;
    root.__ipui = { el: root };

    var key = attr(root, 'data-key', '');
    var store = 'ipui.announce.' + key;

    function seen() {
      try { return window.localStorage.getItem(store) === '1'; }
      catch (e) { return false; }   // private mode, or storage disabled
    }

    if (key && seen()) { root.remove(); return root.__ipui; }
    root.hidden = false;

    var close = root.querySelector('.ipui-announce__close');
    if (close) {
      close.addEventListener('click', function () {
        try { window.localStorage.setItem(store, '1'); } catch (e) {}
        var g = gsapNow();
        if (!g || REDUCED) { root.remove(); return; }
        /* Collapsing the height as well as fading means the page below
           rises to fill the gap instead of leaving a band of nothing. */
        g.to(root, {
          height: 0, opacity: 0, paddingTop: 0, paddingBottom: 0,
          duration: 0.3, ease: 'power2.inOut',
          onComplete: function () { root.remove(); }
        });
      });
    }
    return root.__ipui;
  }

  /* ═══ Pagination ══════════════════════════════════════════════════
     Markup:
       <div class="ipui-pager" data-ipui="pager" data-total="12" data-page="1">
       </div>

     Emits `ipui:page` with the new page; the host does the fetching. The
     digit roll is the stepper's, shared rather than reimplemented — it
     is the same interaction, and two copies would drift.

     Rendered here rather than in the template because the control is
     meaningless without the count, which the host only knows once its
     data has loaded. `inst.set(page, total)` is how it is told. */
  var ARROW_L = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" ' +
    'stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg>';
  var ARROW_R = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" ' +
    'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5l7 7-7 7"/></svg>';

  function mountPager(root) {
    if (root.__ipui) return root.__ipui;

    var inst = { el: root, page: Math.max(1, numAttr(root, 'data-page', 1)),
                 total: Math.max(1, numAttr(root, 'data-total', 1)) };
    var prevText = '';

    root.innerHTML =
      '<button class="ipui-pager__btn" type="button" data-dir="-1" ' +
              'aria-label="Previous page">' + ARROW_L + '</button>' +
      '<span class="ipui-pager__count">' +
        '<span class="ipui-pager__digits" aria-hidden="true"></span>' +
        '<span class="ipui-pager__of" aria-hidden="true">/ <span data-total></span></span>' +
        '<span class="ipui-sr" aria-live="polite" data-live></span>' +
      '</span>' +
      '<button class="ipui-pager__btn" type="button" data-dir="1" ' +
              'aria-label="Next page">' + ARROW_R + '</button>';

    var digits = root.querySelector('.ipui-pager__digits');
    var totalEl = root.querySelector('[data-total]');
    var live = root.querySelector('[data-live]');

    /* Shared with the stepper: same cells, same roll, same direction
       semantics. */
    function rollTo(next, dir) {
      var g = gsapNow();
      if (next.length !== prevText.length || !prevText || !g || REDUCED) {
        digits.textContent = '';
        next.split('').forEach(function (ch) {
          var cell = doc.createElement('span');
          cell.className = 'ipui-stepper__digit';
          var glyph = doc.createElement('span');
          glyph.textContent = ch;
          cell.appendChild(glyph);
          digits.appendChild(cell);
        });
        prevText = next;
        return;
      }
      var cells = digits.querySelectorAll('.ipui-stepper__digit');
      next.split('').forEach(function (ch, i) {
        if (ch === prevText[i] || !cells[i]) return;
        var cell = cells[i];
        var outgoing = cell.firstElementChild;
        var incoming = doc.createElement('span');
        incoming.textContent = ch;
        cell.appendChild(incoming);
        var travel = dir >= 0 ? 20 : -20;
        g.fromTo(incoming,
          { y: travel, opacity: 0, scale: 0.5, filter: 'blur(2px)' },
          { y: 0, opacity: 1, scale: 1, filter: 'blur(0px)',
            duration: 0.4, ease: 'back.out(1.7)' });
        if (outgoing) {
          g.to(outgoing, {
            y: -travel, opacity: 0, scale: 0.5, filter: 'blur(2px)',
            duration: 0.32, ease: 'power2.in',
            onComplete: function () {
              if (outgoing.parentNode) outgoing.parentNode.removeChild(outgoing);
            }
          });
          window.setTimeout(function () {
            if (outgoing.parentNode && outgoing !== cell.lastElementChild) {
              outgoing.parentNode.removeChild(outgoing);
            }
          }, 1200);
        }
      });
      prevText = next;
    }

    function paint(dir) {
      rollTo(String(inst.page), dir || 1);
      totalEl.textContent = String(inst.total);
      /* The digit roll says nothing to a screen reader, and on a paged
         list the number changing is the only confirmation the button
         worked. */
      live.textContent = 'Page ' + inst.page + ' of ' + inst.total;
      root.querySelector('[data-dir="-1"]').disabled = inst.page <= 1;
      root.querySelector('[data-dir="1"]').disabled = inst.page >= inst.total;
    }

    function go(dir) {
      var next = Math.min(inst.total, Math.max(1, inst.page + dir));
      if (next === inst.page) return;
      inst.page = next;
      paint(dir);
      emit(root, 'page', { page: next, total: inst.total });
    }

    Array.prototype.forEach.call(root.querySelectorAll('[data-dir]'), function (b) {
      b.addEventListener('click', function () { go(numAttr(b, 'data-dir', 1)); });
    });

    inst.set = function (page, total) {
      if (total !== undefined) inst.total = Math.max(1, total);
      var dir = page >= inst.page ? 1 : -1;
      inst.page = Math.min(inst.total, Math.max(1, page));
      paint(dir);
    };

    paint(1);
    root.__ipui = inst;
    return inst;
  }

  /* ═══ Registry + mounting ═════════════════════════════════════════ */
  api.components = {
    split: mountSplit,
    stepper: mountStepper,
    save: mountSave,
    morph: mountMorph,
    slider: mountSlider,
    voice: mountVoice,
    dates: mountDates,
    slots: mountSlots,
    undo: mountUndo,
    'pin-list': mountPinList,
    wiggle: mountWiggle,
    announce: mountAnnounce,
    pager: mountPager
  };

  api.get = function (el) { return el ? el.__ipui || null : null; };

  api.mount = function (scope) {
    var root = scope || doc;
    if (!root.querySelectorAll) return;
    Array.prototype.forEach.call(root.querySelectorAll('[data-ipui]'), function (el) {
      var name = el.getAttribute('data-ipui');
      var fn = api.components[name];
      if (!fn) return;
      /* One component throwing must not stop the rest of the page from
         upgrading — a half-mounted page is much harder to diagnose than
         one component that stayed plain. */
      try { fn(el); } catch (e) {
        if (window.console && console.warn) {
          console.warn('[ip-ui] ' + name + ' failed to mount', e);
        }
      }
    });
  };

  function boot() {
    api.mount(doc);

    /* Most of this app renders after a fetch, so a single pass at boot
       finds an empty <main>. childList only: mounting sets attributes,
       so watching those would feed itself. */
    if (window.MutationObserver && doc.body) {
      var pending = 0;
      new MutationObserver(function (records) {
        if (pending) return;
        for (var i = 0; i < records.length; i++) {
          if (records[i].addedNodes.length) {
            pending = window.setTimeout(function () {
              pending = 0;
              api.mount(doc);
            }, 150);
            return;
          }
        }
      }).observe(doc.body, { childList: true, subtree: true });
    }
  }

  if (doc.readyState !== 'loading') boot();
  else doc.addEventListener('DOMContentLoaded', boot);
})();
