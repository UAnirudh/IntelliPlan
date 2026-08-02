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
    var THUMB_PX = 50;   // must match .ipui-slider__thumb width in the CSS

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

      /* The fill runs from the left edge to the centre of the thumb, and
         the thumb travels the track minus its own width — so both are
         expressed against (100% - thumb) rather than against 100%, or
         the thumb overhangs the end of the track at max. */
      if (fill) fill.style.width = 'calc(' + (r * 100) + '% * (1 - ' + THUMB_PX + 'px / 100%) + ' + THUMB_PX + 'px)';
      if (thumb) thumb.style.left = 'calc(' + (r * 100) + '% - ' + (r * THUMB_PX) + 'px)';
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

    paint();
    inst.value = function (v) {
      if (v === undefined) return parseFloat(input.value);
      input.value = v;
      paint();
      return v;
    };
    root.__ipui = inst;
    return inst;
  }

  /* ═══ Registry + mounting ═════════════════════════════════════════ */
  api.components = {
    split: mountSplit,
    stepper: mountStepper,
    save: mountSave,
    morph: mountMorph,
    slider: mountSlider
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
