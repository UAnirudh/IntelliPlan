/* ════════════════════════════════════════════════════════════════════
   Follow-Through — one-tap rescheduling and on-time forecasts.

   The scheduler page owns the plan (_lastScheduleData), the checkbox state
   (_checklistState / stateFor) and the renderers. This file only adds:

   · a "Life happened?" bar: can't study today · less time today ·
     extra time · catch me up
   · a preview sheet — every change is shown with its consequence before it
     is applied, because a plan that rearranges itself silently is a plan
     students stop trusting
   · "Not today" and "Already done" in each block's move menu
   · a "Rebalance around this?" offer after a block is dragged to another day
   · the on-time forecast: how likely each deadline is to land, and what the
     model has learned about how this student actually works

   Server: POST /api/schedule/adjust, GET /api/schedule/forecast.
   Everything degrades to nothing: if the endpoints 404 (kill switch) or
   fail, the bar hides itself and the page is exactly as it was.
═══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var FT = {};
  var state = {
    enabled: true,
    forecast: null,
    loading: false,
    intent: null,
    payload: null,
    preview: null,
    returnFocus: null,
    refreshTimer: null,
    // Autopilot: runs once per page load, when the plan and the student's
    // assignment list have both arrived (or the list is clearly not coming).
    autopilotDone: false,
    assignmentsReady: false,
    autopilotNote: null,       // {headline, reasons[], undo}
  };

  var INTENT_TITLES = {
    skip_day: "Can't study",
    limit_day: 'Less time today',
    add_time: 'Extra time',
    catch_up: "Catch me up",
    push: 'Not today',
    done: 'Already done',
    pin: 'Keep it there',
  };

  // ── helpers ───────────────────────────────────────────────────────

  function esc(s) {
    if (typeof escapeText === 'function') return escapeText(s);
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function planReady() {
    try {
      return !!(_lastScheduleData && (_lastScheduleData.schedule || []).length && _progressSyncEnabled);
    } catch (e) {
      return false;
    }
  }

  function isoDay(offset) {
    var d = new Date();
    d.setDate(d.getDate() + (offset || 0));
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
  }

  function dayLabel(iso) {
    if (iso === isoDay(0)) return 'Today';
    if (iso === isoDay(1)) return 'Tomorrow';
    var d = new Date(iso + 'T12:00:00');
    return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function shortDay(iso) {
    if (!iso) return '';
    if (iso === isoDay(0)) return 'today';
    if (iso === isoDay(1)) return 'tomorrow';
    return new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short' });
  }

  function planSettings() {
    var out = {};
    var hours = document.getElementById('hoursPerDay');
    var time = document.getElementById('preferredTime');
    if (hours && hours.value) out.hours_per_day = parseFloat(hours.value);
    if (time && time.value) out.preferred_time = time.value;
    return out;
  }

  function post(payload) {
    var body = Object.assign({}, planSettings(), payload);
    return fetch('/api/schedule/adjust', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (r) {
      if (r.status === 404) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          if (!b || b.status !== 'none') state.enabled = false;
          return b || { status: 'error' };
        });
      }
      return r.json();
    });
  }

  // ── the bar ───────────────────────────────────────────────────────

  function forecastMarkup() {
    var f = state.forecast;
    if (!f || !f.risk || !(f.risk.tasks || []).length) {
      return state.loading
        ? '<div class="ft-forecast-line ft-muted">Checking your deadlines…</div>'
        : '';
    }
    var tasks = f.risk.tasks;
    var atRisk = tasks.filter(function (t) { return t.status === 'at_risk'; });
    var watch = tasks.filter(function (t) { return t.status === 'watch'; });
    var headline;
    if (!atRisk.length && !watch.length) {
      headline = '<span class="ft-dot ft-dot--ok" aria-hidden="true"></span>' +
        (tasks.length === 1 ? 'Your deadline is on track' : 'All ' + tasks.length + ' deadlines on track');
    } else {
      var bits = [];
      if (atRisk.length) bits.push(atRisk.length + ' at risk');
      if (watch.length) bits.push(watch.length + ' to watch');
      headline = '<span class="ft-dot ' + (atRisk.length ? 'ft-dot--risk' : 'ft-dot--watch') +
        '" aria-hidden="true"></span>' + bits.join(' · ');
    }

    var rows = tasks.slice().sort(function (a, b) {
      return a.on_time_probability - b.on_time_probability;
    }).map(function (t) {
      var why = t.main_risk === 'not_planned'
        ? 'not enough time planned'
        : t.main_risk === 'overrun' ? 'could run long' : t.main_risk === 'follow_through' ? 'sessions might slip' : '';
      return '<li class="ft-task ft-task--' + esc(t.status) + '">' +
        '<span class="ft-task-name">' + esc(t.title) + '</span>' +
        '<span class="ft-task-due">' + (t.due_date ? 'due ' + esc(shortDay(t.due_date)) : '') + '</span>' +
        '<span class="ft-task-pct" title="Chance it is finished on time">' + esc(t.percent) + '%</span>' +
        (t.status !== 'on_track' && why ? '<span class="ft-task-why">' + esc(why) + '</span>' : '') +
        '</li>';
    }).join('');

    var insights = (f.insights || []).map(function (i) {
      return '<li>' + esc(i.text) + '</li>';
    }).join('');
    var learned = insights
      ? '<div class="ft-learned"><div class="ft-learned-title">What IntelliPlan has learned about how you work</div><ul>' + insights + '</ul></div>'
      : '<p class="ft-muted ft-small">Forecasts get personal as you tick blocks off — the plan learns when you really get work done.</p>';

    return '<details class="ft-forecast-details">' +
      '<summary class="ft-forecast-line">' + headline +
      '<span class="ft-forecast-more">Details</span></summary>' +
      '<ul class="ft-tasks">' + rows + '</ul>' + learned +
      '</details>';
  }

  function autopilotEnabled() {
    try {
      var a = _lastScheduleData && _lastScheduleData.autopilot;
      return !(a && a.enabled === false);
    } catch (e) { return true; }
  }

  function autopilotMarkup() {
    var on = autopilotEnabled();
    var note = state.autopilotNote;
    var toggle = '<button type="button" class="ft-chip ft-auto-toggle' + (on ? ' is-on' : '') +
      '" data-ft-autopilot-toggle aria-pressed="' + on + '" title="When on, IntelliPlan moves missed work, adds new assignments and protects at-risk deadlines by itself — always with an undo.">' +
      'Autopilot ' + (on ? 'on' : 'off') + '</button>';
    if (!note) return '<div class="ft-auto">' + toggle + '</div>';
    var reasons = (note.reasons || []).map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('');
    return '<div class="ft-auto ft-auto--acted" role="status">' +
      '<div class="ft-auto-head"><span class="ft-auto-badge">Autopilot</span>' +
      '<strong>' + esc(note.headline) + '</strong></div>' +
      (reasons ? '<ul class="ft-auto-reasons">' + reasons + '</ul>' : '') +
      '<div class="ft-auto-actions">' +
        (note.undo ? '<button type="button" class="ft-btn" data-ft-autopilot-undo>Undo</button>' : '') +
        '<button type="button" class="ft-btn" data-ft-autopilot-dismiss>Got it</button>' + toggle +
      '</div></div>';
  }

  function barMarkup() {
    var missed = state.forecast && state.forecast.missed_minutes;
    var behind = missed > 0
      ? '<span class="ft-badge" title="Planned on earlier days and not ticked off">' + esc(missed) + ' min</span>'
      : '';
    return '<div class="ft-bar">' +
      '<div class="ft-bar-head">' +
        '<div class="ft-title">Life happened?</div>' +
        '<div class="ft-sub">Adjust in one tap. Only what has to move moves, and you see the effect first.</div>' +
      '</div>' +
      '<div class="ft-actions" role="group" aria-label="Adjust your plan">' +
        '<button type="button" class="ft-btn" data-ft-intent="skip_day">Can\'t study today</button>' +
        '<button type="button" class="ft-btn" data-ft-intent="limit_day">Less time today</button>' +
        '<button type="button" class="ft-btn" data-ft-intent="add_time">I have extra time</button>' +
        '<button type="button" class="ft-btn' + (missed > 0 ? ' ft-btn--accent' : '') + '" data-ft-intent="catch_up">I\'m behind ' + behind + '</button>' +
      '</div>' +
      '<div class="ft-forecast" aria-live="polite">' + forecastMarkup() + '</div>' +
      autopilotMarkup() +
    '</div>';
  }

  function renderBar() {
    var mounts = document.querySelectorAll('[data-ft-mount]');
    var show = state.enabled && planReady();
    mounts.forEach(function (m) {
      if (!show) {
        m.hidden = true;
        m.innerHTML = '';
        return;
      }
      var open = !!m.querySelector('details[open]');
      m.hidden = false;
      m.innerHTML = barMarkup();
      if (open) {
        var d = m.querySelector('details');
        if (d) d.open = true;
      }
      m.querySelectorAll('[data-ft-intent]').forEach(function (btn) {
        btn.addEventListener('click', function () { FT.open(btn.dataset.ftIntent, {}, btn); });
      });
      m.querySelectorAll('[data-ft-autopilot-toggle]').forEach(function (btn) {
        btn.addEventListener('click', toggleAutopilot);
      });
      m.querySelectorAll('[data-ft-autopilot-undo]').forEach(function (btn) {
        btn.addEventListener('click', undoAutopilot);
      });
      m.querySelectorAll('[data-ft-autopilot-dismiss]').forEach(function (btn) {
        btn.addEventListener('click', function () { state.autopilotNote = null; renderBar(); });
      });
    });
  }

  FT.refresh = function () {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(loadForecast, 250);
  };

  function loadForecast() {
    if (!state.enabled || !planReady()) { renderBar(); return; }
    state.loading = true;
    renderBar();
    var q = planSettings();
    var qs = Object.keys(q).map(function (k) { return k + '=' + encodeURIComponent(q[k]); }).join('&');
    fetch('/api/schedule/forecast' + (qs ? '?' + qs : ''))
      .then(function (r) {
        if (r.status === 404) { state.enabled = false; return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (body) {
        state.loading = false;
        if (body && body.status === 'ok') state.forecast = body;
        renderBar();
      })
      .catch(function () { state.loading = false; renderBar(); });
  }

  FT.available = function () { return state.enabled && planReady(); };

  // ── the sheet ─────────────────────────────────────────────────────

  function sheet() {
    var overlay = document.getElementById('ftOverlay');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'ftOverlay';
    overlay.className = 'ft-overlay';
    overlay.hidden = true;
    overlay.innerHTML =
      '<div class="ft-sheet" role="dialog" aria-modal="true" aria-labelledby="ftSheetTitle">' +
        '<div class="ft-sheet-head">' +
          '<h3 id="ftSheetTitle" class="ft-sheet-title"></h3>' +
          '<button type="button" class="ft-close" aria-label="Close">&times;</button>' +
        '</div>' +
        '<div class="ft-step" id="ftStep"></div>' +
        '<div class="ft-result" id="ftResult" role="status" aria-live="polite"></div>' +
        '<div class="ft-sheet-actions">' +
          '<button type="button" class="ft-btn ft-cancel">Keep my plan</button>' +
          '<button type="button" class="ft-btn ft-btn--primary ft-apply" disabled>Apply</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', function (e) { if (e.target === overlay) FT.close(); });
    overlay.querySelector('.ft-close').addEventListener('click', FT.close);
    overlay.querySelector('.ft-cancel').addEventListener('click', FT.close);
    overlay.querySelector('.ft-apply').addEventListener('click', apply);
    overlay.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.stopPropagation(); FT.close(); }
      if (e.key === 'Tab') trapFocus(e, overlay);
    });
    return overlay;
  }

  function trapFocus(e, root) {
    var f = [].slice.call(root.querySelectorAll('button:not([disabled]), select, [tabindex="0"]'));
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function chips(name, options, selected) {
    return '<div class="ft-chips" role="radiogroup" aria-label="' + esc(name) + '">' +
      options.map(function (o) {
        var on = String(o.value) === String(selected);
        return '<button type="button" class="ft-chip' + (on ? ' is-on' : '') + '" role="radio" aria-checked="' +
          on + '" data-ft-chip="' + esc(name) + '" data-value="' + esc(o.value) + '">' + esc(o.label) + '</button>';
      }).join('') +
    '</div>';
  }

  function dayOptions(from, count) {
    var out = [];
    for (var i = from; i < from + count; i++) {
      var iso = isoDay(i);
      out.push({ value: iso, label: dayLabel(iso) });
    }
    return out;
  }

  function renderStep() {
    var step = document.getElementById('ftStep');
    var p = state.payload || {};
    var html = '';
    if (state.intent === 'skip_day') {
      html = '<p class="ft-q">Which day is off?</p>' + chips('day', dayOptions(0, 7), p.day);
    } else if (state.intent === 'limit_day') {
      html = '<p class="ft-q">How much time do you actually have today?</p>' +
        chips('minutes', [15, 30, 45, 60, 90].map(function (m) {
          return { value: m, label: m < 60 ? m + ' min' : (m / 60) + (m === 60 ? ' hour' : ' hours') };
        }), p.minutes);
    } else if (state.intent === 'add_time') {
      html = '<p class="ft-q">Which day?</p>' + chips('day', dayOptions(0, 7), p.day) +
        '<p class="ft-q">How much extra?</p>' +
        chips('minutes', [30, 60, 90, 120, 180].map(function (m) {
          return { value: m, label: m < 60 ? m + ' min' : (m / 60) + (m === 60 ? ' hour' : ' hours') };
        }), p.minutes);
    } else if (state.intent === 'push') {
      html = '<p class="ft-q">Not before…</p>' + chips('day', dayOptions(1, 6), p.day);
    } else if (p._title) {
      html = '<p class="ft-q">' + esc(p._title) + '</p>';
    }
    step.innerHTML = html;
    step.querySelectorAll('[data-ft-chip]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var key = chip.dataset.ftChip;
        var value = chip.dataset.value;
        state.payload[key] = key === 'minutes' ? parseInt(value, 10) : value;
        renderStep();
        maybePreview();
        var again = document.querySelector('[data-ft-chip="' + key + '"][data-value="' + value + '"]');
        if (again) again.focus();
      });
    });
  }

  function ready() {
    var p = state.payload || {};
    if (state.intent === 'limit_day') return p.minutes != null;
    if (state.intent === 'add_time') return !!p.day && p.minutes != null;
    return true;
  }

  function wire(p) {
    var out = {};
    Object.keys(p).forEach(function (k) { if (k.charAt(0) !== '_') out[k] = p[k]; });
    out.action = state.intent;
    return out;
  }

  function maybePreview() {
    var result = document.getElementById('ftResult');
    var applyBtn = document.querySelector('#ftOverlay .ft-apply');
    applyBtn.disabled = true;
    state.preview = null;
    if (!ready()) { result.innerHTML = ''; return; }
    result.innerHTML = '<div class="ft-muted">Working out what changes…</div>';
    var seq = (state.seq = (state.seq || 0) + 1);
    post(Object.assign(wire(state.payload), { preview: true }))
      .then(function (body) {
        if (seq !== state.seq) return;
        if (!body || body.status !== 'ok') {
          result.innerHTML = '<div class="ft-error">' + esc((body && body.message) || 'Could not work that out right now.') + '</div>';
          return;
        }
        state.preview = body;
        result.innerHTML = previewMarkup(body);
        applyBtn.disabled = false;
      })
      .catch(function () {
        if (seq !== state.seq) return;
        result.innerHTML = '<div class="ft-error">You look offline — nothing was changed.</div>';
      });
  }

  function pctPair(before, after) {
    if (before == null || after == null) return after != null ? esc(after) + '%' : '';
    var cls = after > before ? 'ft-up' : after < before ? 'ft-down' : '';
    return '<span class="ft-pct ' + cls + '">' + esc(before) + '% → ' + esc(after) + '%</span>';
  }

  function previewMarkup(body) {
    var changes = (body.changes || []).map(function (c) {
      var what;
      if (c.kind === 'moved') {
        what = (c.from || []).map(shortDay).join(', ') + ' → ' + (c.to || []).map(shortDay).join(', ');
      } else if (c.kind === 'added') {
        what = 'now on ' + (c.to || []).map(shortDay).join(', ');
      } else if (c.kind === 'removed') {
        what = 'off the plan';
      } else {
        what = 'won\'t fully fit';
      }
      return '<li class="ft-change ft-change--' + esc(c.kind) + '">' +
        '<span class="ft-change-title">' + esc(c.title) + '</span>' +
        '<span class="ft-change-what">' + esc(what) + '</span>' +
        pctPair(c.before_percent, c.after_percent) + '</li>';
    }).join('');
    // "Before this change → after": where the student was, and where they
    // are now. Percent chance, weighted by how much each deadline matters.
    var risk = body.risk || {};
    var was = risk.before, after = risk.after;
    var safety = '';
    if (was && after) {
      safety = '<div class="ft-safety">Chance of hitting every deadline ' +
        pctPair(Math.round(was.weighted_on_time * 100), Math.round(after.weighted_on_time * 100)) +
        '<span class="ft-muted"> before → after this change</span></div>';
    }
    return '<div class="ft-headline">' + esc(body.headline) + '</div>' +
      (body.detail ? '<div class="ft-detail">' + esc(body.detail) + '</div>' : '') +
      safety +
      (changes ? '<ul class="ft-changes">' + changes + '</ul>' : '');
  }

  function apply() {
    var applyBtn = document.querySelector('#ftOverlay .ft-apply');
    if (!state.preview) return;
    applyBtn.disabled = true;
    applyBtn.textContent = 'Applying…';
    post(wire(state.payload))
      .then(function (body) {
        applyBtn.textContent = 'Apply';
        if (!body || body.status !== 'ok' || !body.data) {
          document.getElementById('ftResult').innerHTML =
            '<div class="ft-error">' + esc((body && body.message) || 'Could not apply that. Your plan is unchanged.') + '</div>';
          applyBtn.disabled = false;
          return;
        }
        adopt(body);
        FT.close();
        if (window.IP && IP.toast) IP.toast(body.headline + (body.detail ? ' ' + body.detail : ''), 'success');
        if (typeof ivAnnounce === 'function') ivAnnounce(body.headline);
      })
      .catch(function () {
        applyBtn.textContent = 'Apply';
        applyBtn.disabled = false;
        document.getElementById('ftResult').innerHTML = '<div class="ft-error">You look offline — nothing was changed.</div>';
      });
  }

  /* Take the server's plan as the page's plan. Block ids are fresh, so the
     only checkbox state that applies is the progress the server sent back
     (today's already-finished blocks). */
  function adopt(body) {
    _lastScheduleData = body.data;
    var progress = body.progress || {};
    Object.keys(progress).forEach(function (id) {
      var entry = progress[id] || {};
      var s = stateFor(id);
      s.done = !!(entry === true || entry.done);
      s.checked = Array.isArray(entry.checked) ? entry.checked : [];
    });
    try { localStorage.setItem(CK_STATE_KEY, JSON.stringify(_checklistState)); } catch (e) {}
    if (typeof renderSchedule === 'function') renderSchedule(_lastScheduleData);
    if (typeof renderInteractive === 'function') renderInteractive();
    if (typeof updateBadge === 'function') updateBadge();
    if (body.forecast || (body.risk && body.risk.after)) state.forecast = null;
    FT.refresh();
  }

  FT.open = function (intent, payload, trigger) {
    if (!FT.available()) return;
    state.intent = intent;
    state.payload = Object.assign({}, payload || {});
    if (intent === 'skip_day' && !state.payload.day) state.payload.day = isoDay(0);
    if (intent === 'add_time' && !state.payload.day) state.payload.day = isoDay(0);
    if (intent === 'push' && !state.payload.day) state.payload.day = isoDay(1);
    state.returnFocus = trigger || document.activeElement;
    var overlay = sheet();
    document.getElementById('ftSheetTitle').textContent =
      (INTENT_TITLES[intent] || 'Adjust') + (state.payload._title && intent !== 'done' ? ' — ' + state.payload._title : '');
    document.getElementById('ftResult').innerHTML = '';
    overlay.hidden = false;
    document.body.classList.add('ft-open');
    renderStep();
    maybePreview();
    var first = overlay.querySelector('.ft-chip.is-on, .ft-chip, .ft-cancel');
    if (first) first.focus();
  };

  FT.close = function () {
    var overlay = document.getElementById('ftOverlay');
    if (!overlay || overlay.hidden) return;
    overlay.hidden = true;
    document.body.classList.remove('ft-open');
    state.seq = (state.seq || 0) + 1;   // drop any preview still in flight
    if (state.returnFocus && document.contains(state.returnFocus)) state.returnFocus.focus();
    state.returnFocus = null;
  };

  // ── entry points from the rest of the page ────────────────────────

  function blockTask(blockId) {
    var b = typeof findBlock === 'function' ? findBlock(blockId) : null;
    if (!b) return null;
    return {
      block: b,
      task_id: b.task_id || b.parent_title || b.assignment,
      title: b.parent_title || b.assignment || 'this',
    };
  }

  /* Extra items for the per-block move menu. */
  FT.menuItems = function () {
    if (!FT.available()) return [];
    var icon = function (d) {
      return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + d + '</svg>';
    };
    return [
      { action: 'ft-push', label: 'Not today — push it', svg: icon('<path d="M5 12h14"/><polyline points="13 6 19 12 13 18"/>'), enabled: true },
      { action: 'ft-done', label: 'Already done — take it off', svg: icon('<polyline points="20 6 9 17 4 12"/>'), enabled: true },
    ];
  };

  FT.fromMenu = function (action, blockId, trigger) {
    var t = blockTask(blockId);
    if (!t) return;
    if (action === 'ft-push') FT.open('push', { task_id: t.task_id, _title: t.title }, trigger);
    if (action === 'ft-done') FT.open('done', { task_id: t.task_id, _title: 'Take “' + t.title + '” off the plan?' }, trigger);
  };

  /* After a drag to another day: offer to rebalance the week around it. */
  FT.offerRebalance = function (block, fromDate, toDate) {
    if (!FT.available() || !block || !fromDate || !toDate || fromDate === toDate) return;
    if (!(window.IP && IP.toast)) return;
    IP.toast('Moved to ' + shortDay(String(toDate).slice(0, 10)) + '. Rebalance the rest of your week around it?', 'info', {
      action: 'Rebalance',
      duration: 8000,
      onAction: function () {
        FT.open('pin', {
          task_id: block.task_id || block.parent_title || block.assignment,
          day: String(toDate).slice(0, 10),
          from_day: String(fromDate).slice(0, 10),
          minutes: parseInt(block.duration_minutes, 10) || undefined,
          _title: block.parent_title || block.assignment,
        });
      },
    });
  };

  // ── autopilot ─────────────────────────────────────────────────────

  function currentAssignments() {
    try { return Array.isArray(allAssignments) ? allAssignments : []; } catch (e) { return []; }
  }

  function runAutopilot() {
    if (state.autopilotDone || !state.enabled || !planReady() || !autopilotEnabled()) return;
    state.autopilotDone = true;
    fetch('/api/schedule/autopilot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({}, planSettings(), { assignments: currentAssignments() })),
    })
      .then(function (r) {
        if (r.status === 404) { state.enabled = false; return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (body) {
        if (!body || !body.acted || !body.data) return;
        state.autopilotNote = { headline: body.headline, reasons: body.reasons || [], undo: !!body.undo_available };
        adopt(body);
        if (typeof ivAnnounce === 'function') ivAnnounce(body.headline);
      })
      .catch(function () {});
  }

  /* Called by the page when the plan and the assignment list arrive. Waits
     for both, but not forever: a student with no LMS still has missed
     sessions worth moving. */
  FT.planLoaded = function () {
    FT.refresh();
    if (state.assignmentsReady) runAutopilot();
    else setTimeout(runAutopilot, 4000);
  };
  FT.assignmentsLoaded = function () {
    state.assignmentsReady = true;
    if (planReady()) runAutopilot();
  };

  function undoAutopilot() {
    fetch('/api/schedule/autopilot/undo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (!body || body.status !== 'ok' || !body.data) {
          if (window.IP && IP.toast) IP.toast((body && body.message) || 'Could not undo that.', 'error');
          return;
        }
        state.autopilotNote = null;
        adopt(body);
        if (window.IP && IP.toast) IP.toast('Your previous plan is back. Autopilot will leave it alone for today.', 'success');
      })
      .catch(function () {
        if (window.IP && IP.toast) IP.toast('You look offline — nothing was changed.', 'error');
      });
  }

  function toggleAutopilot() {
    var next = !autopilotEnabled();
    fetch('/api/schedule/autopilot/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: next }),
    })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (!body || body.status !== 'ok') return;
        try {
          _lastScheduleData.autopilot = Object.assign({}, _lastScheduleData.autopilot || {}, { enabled: next });
        } catch (e) {}
        renderBar();
        if (window.IP && IP.toast) {
          IP.toast(next ? 'Autopilot is on — your plan will keep itself up to date.' : 'Autopilot is off — your plan only changes when you change it.', 'info');
        }
      })
      .catch(function () {});
  }

  window.FT = FT;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', FT.refresh);
  } else {
    FT.refresh();
  }
})();
