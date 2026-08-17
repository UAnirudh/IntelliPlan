/* ══════════════════════════════════════════════════════════════
   Command Center — client-side hydration
   All data loads async via parallel fetch after the shell renders.
═══════════════════════════════════════════════════════════════ */
var STAGE_SVG = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><use href="#i-sparkle"/></svg>';
var TZ = (typeof Intl !== 'undefined') ? Intl.DateTimeFormat().resolvedOptions().timeZone : 'UTC';

document.addEventListener('DOMContentLoaded', function() {
  document.body.classList.add('has-side');
  updateLiveTime();
  setInterval(updateLiveTime, 60000);

  document.querySelectorAll('.cc-care-btn[data-action]').forEach(function(btn) {
    btn.addEventListener('click', function() { doCareAction(btn.dataset.action); });
  });
  var chest = document.getElementById('ccChestBtn');
  if (chest) chest.addEventListener('click', openChest);

  var evolveOverlay = document.getElementById('ccEvolveOverlay');
  if (evolveOverlay) evolveOverlay.addEventListener('click', function(e) {
    if (e.target === evolveOverlay) closeEvolveOverlay();
  });

  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeEvolveOverlay();
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      var input = document.getElementById('ccChatInput');
      if (input) input.focus();
    }
  });

  if (window.innerWidth >= 1100) {
    var input = document.getElementById('ccChatInput');
    if (input) setTimeout(function(){ input.focus(); }, 200);
  }

  var backdrop = document.getElementById('mobileNavBackdrop');
  if (backdrop) backdrop.addEventListener('click', closeMobileNav);

  planiUpdateSend();
  ccBootstrap();
});

/* ══════════════════════════════════════════════════════════════
   BOOTSTRAP — fire ALL fetches in parallel, hydrate as they land
═══════════════════════════════════════════════════════════════ */
function ccBootstrap() {
  var todayP   = fetch('/api/today',              { credentials:'same-origin' });
  var petP     = fetch('/api/pet/status',          { credentials:'same-origin' });
  var streakP  = fetch('/api/streak/risk',         { credentials:'same-origin' });

  todayP
    .then(function(r) { if (!r.ok) throw r; return r.json(); })
    .then(hydrateToday)
    .catch(function(e) {
      console.warn('[cc] today fetch failed, retrying…', e);
      return new Promise(function(resolve) { setTimeout(resolve, 1500); })
        .then(function() { return fetch('/api/today', { credentials:'same-origin' }); })
        .then(function(r) { if (!r.ok) throw r; return r.json(); })
        .then(hydrateToday)
        .catch(function(e2) {
          console.warn('[cc] today retry also failed', e2);
          ccShowError("We couldn't load your day. Refresh the page to try again.");
        });
    });

  petP
    .then(function(r) { if (!r.ok) throw r; return r.json(); })
    .then(hydrateMomentumData)
    .catch(function() {});

  streakP
    .then(function(r) { if (!r.ok) throw r; return r.json(); })
    .then(hydrateStreakRisk)
    .catch(function() {});

  lgLazyInit();
}

var _lgLoaded = false;
function lgLazyInit() {
  var section = document.getElementById('ccLearning');
  if (!section) return;
  if (!('IntersectionObserver' in window)) { lgLoad(); return; }
  var obs = new IntersectionObserver(function(entries) {
    if (entries[0].isIntersecting && !_lgLoaded) {
      _lgLoaded = true;
      obs.disconnect();
      lgLoad();
    }
  }, { rootMargin: '200px' });
  obs.observe(section);
}

function ccShowError(msg) {
  var banner = document.getElementById('ccErrorBanner');
  var span = document.getElementById('ccErrorMsg');
  if (banner && span) {
    span.textContent = msg;
    banner.hidden = false;
    banner.style.display = 'flex';
  }
}

/* ── Hydrate the main /api/today payload ── */
function hydrateToday(d) {
  var s = d.student || {};
  var b = d.briefing || {};
  var h = d.health || {};
  var plan = d.plan || [];
  var fc = d.forecast || {};

  setText('ccHeroStatus', b.tone || 'focused');
  var headline = document.getElementById('ccHeroHeadline');
  if (headline) headline.textContent = (s.greeting || 'Hello') + ', ' + (s.name || 'Commander') + '.';
  var body = document.getElementById('ccHeroBody');
  if (body) body.textContent = b.body || '';
  var meta = document.getElementById('ccHeroMeta');
  if (meta) meta.textContent = (b.generated_by || '') + (b.cached ? ' · cached' : '');

  var greeting = document.getElementById('planiWelcomeGreeting');
  if (greeting) {
    var first = (s.name || '').split(' ')[0] || 'Commander';
    greeting.textContent = 'How can I help, ' + first + '?';
  }

  var chatSub = document.getElementById('ccChatSub');
  if (chatSub) chatSub.innerHTML = '<span class="cc-chat-status-dot"></span>connected · ' + plan.length + ' tasks · health ' + (h.score || '—');

  setText('ccOrbHealthNum', h.score || '—');
  var healthOrb = document.getElementById('ccOrbHealthNum');
  if (healthOrb && h.tier) { healthOrb.className = 'cc-orb-num cc-health-' + h.tier; }
  setText('ccOrbTaskNum', plan.length);

  hydrateTaskList(plan);
  hydrateForecast(fc);
  hydrateHealth(h);
}

/* ── Task list ── */
function hydrateTaskList(plan) {
  var container = document.getElementById('ccPlanContainer');
  var metaEl = document.getElementById('ccPlanMeta');
  if (!container) return;
  if (metaEl) metaEl.textContent = plan.length + (plan.length === 1 ? ' task' : ' tasks');

  if (!plan.length) {
    container.innerHTML = '<div class="cc-empty-state">'
      + '<div class="cc-empty-icon"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><use href="#i-done"/></svg></div>'
      + '<h3 class="cc-empty-title">All clear today</h3>'
      + '<p class="cc-empty-body">No upcoming assignments. <a class="cc-link" href="/dashboard">Add one</a>.</p>'
      + '</div>';
    return;
  }

  var html = '<ol class="cc-task-list" id="ccTaskList">';
  plan.forEach(function(task, i) {
    var p = task.priority || {};
    var tier = p.tier || 'low';
    html += '<li class="cc-task-card" id="cc-task-' + i + '"'
      + ' data-task-title="' + escapeAttr(task.title) + '"'
      + ' data-task-source="' + escapeAttr(task.source || 'manual') + '"'
      + ' data-task-ref="' + escapeAttr(task.source_ref || '') + '"'
      + ' style="--delay:' + (i * 40) + 'ms">'
      + '<div class="cc-task-rail cc-tier-' + tier + '"></div>'
      + '<div class="cc-task-main">'
      + '<div class="cc-score-chip cc-tier-' + tier + '"><span class="cc-score-num">' + (p.score || 0) + '</span></div>'
      + '<div class="cc-task-info">'
      + '<h3 class="cc-task-title">' + escapeHtml(task.title) + '</h3>'
      + '<div class="cc-task-details">'
      + '<span class="cc-task-pill">' + escapeHtml(task.course) + '</span>'
      + '<span class="cc-task-pill cc-task-pill-due">' + escapeHtml(task.due_date || 'no due date') + '</span>';
    if (task.est_minutes) html += '<span class="cc-task-pill">' + task.est_minutes + 'min</span>';
    if (task.source && task.source !== 'manual') html += '<span class="cc-task-pill cc-task-pill-src">' + escapeHtml(task.source) + '</span>';
    html += '</div>'
      + '<p class="cc-task-why-now">' + escapeHtml(task.why_now || '') + '</p>'
      + '</div>'
      + '<div class="cc-task-actions">'
      + '<button type="button" class="cc-task-btn cc-task-btn-ask" onclick="ccAskAboutTask(this)" title="Ask about this"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></button>'
      + '<button type="button" class="cc-task-btn cc-task-btn-why" aria-expanded="false" onclick="ccToggleWhy(this)" title="Why this priority?"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg></button>'
      + '<button type="button" class="cc-task-btn cc-task-btn-dismiss" onclick="ccDismissTask(this)" title="Mark done"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></button>'
      + '</div></div>';

    var rationale = (p.rationale || []);
    html += '<div class="cc-rationale" hidden><div class="cc-rationale-chips">';
    rationale.forEach(function(chip) {
      html += '<div class="cc-reason-chip">'
        + '<span class="cc-reason-key">' + escapeHtml(chip.key) + '</span>'
        + '<span class="cc-reason-weight">+' + chip.weight + '</span>'
        + '<span class="cc-reason-text">' + escapeHtml(chip.reason) + '</span>'
        + '</div>';
    });
    html += '</div></div></li>';
  });
  html += '</ol>';
  container.innerHTML = html;
}

/* ── Forecast bars ── */
function hydrateForecast(fc) {
  var bars = document.getElementById('ccForecastBars');
  var summary = document.getElementById('ccForecastSummary');
  if (!bars) return;
  var days = fc.days || [];
  if (!days.length) { bars.innerHTML = ''; return; }
  bars.innerHTML = days.map(function(day) {
    var stress = day.stress || 0;
    var cls = stress >= 0.8 ? 'cc-bar-hot' : stress >= 0.5 ? 'cc-bar-warm' : '';
    return '<div class="cc-bar-col" title="' + day.committed_min + 'min / ' + day.available_min + 'min">'
      + '<div class="cc-bar-track"><div class="cc-bar-fill ' + cls + '" style="transform:scaleY(' + stress + ')"></div></div>'
      + '<span class="cc-bar-label">' + (day.date || '').slice(-5) + '</span></div>';
  }).join('');
  if (summary) summary.textContent = fc.summary || '';
}

/* ── Health card ── */
function hydrateHealth(h) {
  var ring = document.getElementById('ccHealthRing');
  if (ring) ring.style.setProperty('--health-pct', h.score || 0);
  var num = document.getElementById('ccHealthNum');
  if (num) { num.textContent = h.score || '—'; num.className = 'cc-health-num cc-health-' + (h.tier || ''); }
  setText('ccHealthTier', h.tier || '');
  var delta = document.getElementById('ccHealthDelta');
  if (delta && h.delta_vs_yesterday != null) {
    var d = h.delta_vs_yesterday;
    delta.textContent = (d > 0 ? '+' : '') + d + ' today';
    delta.className = 'cc-health-delta' + (d > 0 ? ' cc-delta-up' : d < 0 ? ' cc-delta-down' : '');
  }
  setText('ccHealthSummary', h.summary || '');

  var comps = h.components || [];
  var details = document.getElementById('ccHealthDetails');
  var list = document.getElementById('ccComponentList');
  if (details && list && comps.length) {
    details.hidden = false;
    list.innerHTML = comps.map(function(c) {
      var cls = c.impact > 0 ? 'cc-delta-up' : c.impact < 0 ? 'cc-delta-down' : '';
      /* An impact of exactly 0 is informational (see health.py — the
         completion component is emitted with impact 0 below the bonus
         threshold). "+0" reads like the score moved and it did not. */
      var impact = c.impact > 0 ? '+' + c.impact : c.impact < 0 ? String(c.impact) : '—';
      return '<div class="cc-component-row">'
        + '<span class="cc-component-label">' + escapeHtml(healthComponentLabel(c.key)) + '</span>'
        + '<span class="cc-component-delta ' + cls + '">' + impact + '</span>'
        + '<span class="cc-component-note">' + escapeHtml(c.reason) + '</span></div>';
    }).join('');
  }
}

/* The API's component keys are internal identifiers, and they were being
   printed to the user as-is — "schedule_balance", "declining_courses".
   Map the known ones to real names, and fall back to de-snake-casing
   anything added later so a new key degrades to readable rather than to
   raw. */
/* Keys as emitted by intelliplan/intelligence/health.py — keep in step
   with the key="…" arguments there. */
var CC_HEALTH_LABELS = {
  overdue: 'Overdue work',
  high_stakes_soon: 'Big items due soon',
  failing_courses: 'Failing courses',
  declining_courses: 'Slipping grades',
  completion_7d: 'Completion rate',
  schedule_balance: 'Schedule balance'
};
function healthComponentLabel(key) {
  if (!key) return '';
  if (CC_HEALTH_LABELS[key]) return CC_HEALTH_LABELS[key];
  var words = String(key).replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/* ── Pet / Momentum data ── */
function hydrateMomentumData(d) {
  if (d.status !== 'ok') return;
  setText('ccOrbStreakNum', d.streak || 0);
  setHtml('ccOrbPetIcon', STAGE_SVG);
  setText('ccOrbPetLvl', d.level || 1);
  setText('ccOrbPetLbl', d.name || 'Pet');
  setText('ccMomentumStreakNum', d.streak || 0);
  setHtml('ccMomentumPetEmoji', STAGE_SVG);
  setText('ccMomentumPetName', d.name || 'Pet');
  setText('ccMomentumPetLvl', d.level || 1);
  var fill = document.getElementById('ccMomentumPetFill');
  if (fill) fill.style.transform = 'scaleX(' + (d.progress_to_next || 0) + ')';

  if (d.care) Object.entries(d.care).forEach(function(e) {
    var action = e[0], info = e[1];
    var btn = document.querySelector('.cc-care-btn[data-action="'+action+'"]');
    if (!btn) return;
    btn.classList.toggle('cc-care-cooldown', !info.ready);
    btn.title = info.ready ? info.label + ' (+' + info.xp + ' XP)'
      : info.label + ' on cooldown · ' + Math.ceil(info.wait_seconds/60) + 'm';
  });
  if (d.chest) {
    var chestBtn = document.getElementById('ccChestBtn');
    if (chestBtn) {
      chestBtn.classList.toggle('cc-care-cooldown', !d.chest.ready);
      chestBtn.title = d.chest.ready
        ? 'Daily chest · +' + d.chest.next_reward.xp + ' XP (day ' + d.chest.next_reward.day + ')'
        : 'Chest already claimed today';
    }
  }
}

function hydrateMomentum() {
  fetch('/api/pet/status', { credentials:'same-origin' })
    .then(function(r) { if (!r.ok) throw r; return r.json(); })
    .then(hydrateMomentumData)
    .catch(function() {});
}

/* ── Streak risk ── */
function hydrateStreakRisk(d) {
  if (d.status !== 'ok') return;
  var banner = document.getElementById('ccRiskBanner');
  if (!banner) return;
  if (d.level === 'danger' || d.level === 'broken') {
    banner.hidden = false; banner.style.display = 'flex';
    banner.className = 'cc-risk-banner cc-risk-' + d.level;
    banner.innerHTML = '<span class="cc-risk-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#i-warning"/></svg></span><span>' + d.message + '</span><a href="/dashboard" class="cc-risk-cta">Save it</a>';
  } else if (d.perfect_week && d.perfect_week.perfect && d.perfect_week_paid) {
    banner.hidden = false; banner.style.display = 'flex';
    banner.className = 'cc-risk-banner cc-risk-celebrate';
    banner.innerHTML = '<span class="cc-risk-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#i-sparkle"/></svg></span><span>Perfect week · +' + d.perfect_week_paid + ' XP</span>';
    setTimeout(function() { banner.hidden = true; banner.style.display = 'none'; }, 8000);
  }
}

/* ══════════════════════════════════════════════════════════════
   UTILITIES
═══════════════════════════════════════════════════════════════ */
function setText(id, val) { var el = document.getElementById(id); if (el) el.textContent = String(val); }
function setHtml(id, val) { var el = document.getElementById(id); if (el) el.innerHTML = val; }
function escapeHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escapeAttr(s) { return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* ── Toast ── */
var _toastTimer = null;
function ccToast(msg, kind) {
  var el = document.getElementById('ccToast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'cc-toast cc-toast-' + (kind || 'info');
  el.hidden = false;
  el.style.display = 'flex';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(function() { el.hidden = true; el.style.display = 'none'; }, 2600);
}
window.intelliplanShowPetToast = function(m) { ccToast(m, 'pet'); };

/* ── Live time ── */
function updateLiveTime() {
  var el = document.getElementById('ccLiveTime');
  if (!el) return;
  var now = new Date();
  el.textContent = now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    + ' · ' + now.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
}

/* ── Hero re-scan ── */
function ccRescanDay() {
  var btn = document.getElementById('ccRescanBtn');
  var label = document.getElementById('ccRescanLabel');
  var spinner = document.getElementById('ccRefreshSpinner');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  if (label) label.textContent = 'Re-scanning…';
  if (spinner) spinner.classList.add('cc-spin');
  fetch('/api/today/refresh', { method:'POST', credentials:'same-origin',
    headers: { 'Content-Type':'application/json' }, body: '{}' })
    .then(function(r) { if (!r.ok) throw r; return r.json(); })
    .then(function(d) { hydrateToday(d); btn.disabled = false; if (label) label.textContent = 'Re-scan day'; if (spinner) spinner.classList.remove('cc-spin'); ccToast('Refreshed', 'success'); })
    .catch(function() {
      btn.disabled = false;
      if (label) label.textContent = 'Try again';
      if (spinner) spinner.classList.remove('cc-spin');
      ccToast('Re-scan failed — try again', 'error');
    });
}

/* ── Task: expand rationale ── */
function ccToggleWhy(btn) {
  var card = btn.closest('.cc-task-card');
  if (!card) return;
  var rationale = card.querySelector('.cc-rationale');
  var isOpen = btn.getAttribute('aria-expanded') === 'true';
  if (rationale) rationale.hidden = isOpen;
  btn.setAttribute('aria-expanded', String(!isOpen));
  card.classList.toggle('cc-task-card-open', !isOpen);
}

/* ── Task: ask Plani about it ── */
function ccAskAboutTask(btn) {
  var card = btn.closest('.cc-task-card');
  if (!card) return;
  var title = card.dataset.taskTitle || 'this task';
  var input = document.getElementById('ccChatInput');
  if (input) {
    input.value = 'Help me with: ' + title;
    planiAutosize(input);
    planiUpdateSend();
    input.focus();
  }
  planiSend();
}

/* ── Task: dismiss / mark done ── */
function ccDismissTask(btn) {
  var card = btn.closest('.cc-task-card');
  if (!card) return;
  var title = card.dataset.taskTitle;
  var source = card.dataset.taskSource;
  var ref = card.dataset.taskRef;
  if (!title) return;
  card.classList.add('cc-task-leaving');

  var url, body;
  if (source === 'manual' && ref) {
    url = '/tasks/manual/update';
    body = JSON.stringify({ id: parseInt(ref, 10), done: true, timezone: TZ });
  } else {
    url = '/dismiss';
    body = JSON.stringify({ title: title, timezone: TZ });
  }

  fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin', body: body })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.status === 'ok') {
        ccToast('Marked done', 'success');
        setTimeout(function() {
          card.remove();
          var list = document.getElementById('ccTaskList');
          if (list) {
            var remaining = list.children.length;
            var meta = document.getElementById('ccPlanMeta');
            if (meta) meta.textContent = remaining + (remaining === 1 ? ' task' : ' tasks');
            if (remaining === 0) {
              var empty = document.createElement('div');
              empty.className = 'cc-empty-state';
              empty.innerHTML = '<div class="cc-empty-icon"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5"><use href="#i-done"/></svg></div><h3 class="cc-empty-title">All clear today</h3><p class="cc-empty-body">Every task handled. Take the win.</p>';
              list.replaceWith(empty);
            }
          }
          hydrateMomentum();
        }, 280);
      } else {
        card.classList.remove('cc-task-leaving');
        ccToast('Could not save — try again', 'error');
      }
    })
    .catch(function() {
      card.classList.remove('cc-task-leaving');
      ccToast('Network error', 'error');
    });
}

/* ── Care actions ── */
function doCareAction(action) {
  var btn = document.querySelector('.cc-care-btn[data-action="'+action+'"]');
  if (!btn || btn.classList.contains('cc-care-cooldown')) return;
  btn.classList.add('cc-care-pulse');
  fetch('/api/pet/care', { method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ action: action }) })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.status === 'ok') {
        ccToast(d.copy, 'pet');
        if (d.evolution) showEvolution(d.evolution);
        hydrateMomentum();
      } else if (d.status === 'cooldown') {
        ccToast('On cooldown · ' + Math.ceil(d.wait_seconds/60) + 'm', 'info');
      }
    })
    .catch(function() {});
  setTimeout(function(){ btn.classList.remove('cc-care-pulse'); }, 600);
}

function openChest() {
  var btn = document.getElementById('ccChestBtn');
  if (!btn || btn.classList.contains('cc-care-cooldown')) {
    ccToast('Come back tomorrow for the next chest', 'info'); return;
  }
  btn.classList.add('cc-chest-open');
  fetch('/api/pet/chest', { method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:'{}' })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.status === 'ok') {
        ccToast('+' + d.chest.xp + ' XP · ' + d.chest.tier + ' chest · day ' + d.chest.day, 'pet');
        if (d.evolution) showEvolution(d.evolution);
        hydrateMomentum();
      }
    })
    .catch(function() {});
  setTimeout(function(){ btn.classList.remove('cc-chest-open'); }, 800);
}

function showEvolution(evo) {
  if (!evo) return;
  setHtml('ccEvolveEmoji', STAGE_SVG);
  setText('ccEvolveHeadline', evo.headline || 'Your pet evolved.');
  setText('ccEvolveCopy', evo.copy || '');
  var ov = document.getElementById('ccEvolveOverlay');
  ov.hidden = false; ov.style.display = 'flex';
}
function closeEvolveOverlay() {
  var ov = document.getElementById('ccEvolveOverlay');
  if (ov) { ov.hidden = true; ov.style.display = 'none'; }
}

/* ── Mobile nav ── */
function openMobileNav() {
  var d = document.getElementById('mobileNavDrawer');
  var b = document.getElementById('mobileNavBackdrop');
  if (d) { d.classList.add('cc-mobile-drawer-open'); d.setAttribute('aria-hidden','false'); }
  if (b) { b.hidden = false; b.style.display = 'block'; }
}
function closeMobileNav() {
  var d = document.getElementById('mobileNavDrawer');
  var b = document.getElementById('mobileNavBackdrop');
  if (d) { d.classList.remove('cc-mobile-drawer-open'); d.setAttribute('aria-hidden','true'); }
  if (b) { b.hidden = true; b.style.display = 'none'; }
}

/* ══════════════════════════════════════════════════════════════
   PLANI CHAT
═══════════════════════════════════════════════════════════════ */
var ccChatHistory = [];
var planiSending = false;

function planiAutosize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}
function planiUpdateSend() {
  var input = document.getElementById('ccChatInput');
  var btn = document.getElementById('ccChatSendBtn');
  if (!input || !btn) return;
  btn.disabled = !input.value.trim() || planiSending;
}

function planiNew() {
  ccChatHistory = [];
  var container = document.getElementById('ccChatMessages');
  if (container) container.innerHTML = '';
  var welcome = document.createElement('div');
  welcome.className = 'cc-chat-welcome';
  welcome.id = 'planiWelcome';
  welcome.innerHTML = document.querySelector('.cc-chat-welcome')
    ? document.querySelector('.cc-chat-welcome').innerHTML : '';
  ccToast('New conversation', 'info');
}

function planiSuggest(text) {
  var input = document.getElementById('ccChatInput');
  if (input) { input.value = text; planiAutosize(input); planiUpdateSend(); }
  planiSend();
}

function planiAddMsg(role, text) {
  var container = document.getElementById('ccChatMessages');
  var welcome = document.getElementById('planiWelcome');
  if (welcome) welcome.style.display = 'none';
  var div = document.createElement('div');
  div.className = 'cc-msg cc-msg-' + role;
  if (role === 'bot') div.innerHTML = planiFormatReply(text);
  else if (role === 'action') {
    div.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg><span>' + escapeHtml(text.replace(/^[✓→] ?/, '')) + '</span>';
  } else {
    div.textContent = text;
  }
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function planiFormatReply(text) {
  var escaped = escapeHtml(text);
  escaped = escaped.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  escaped = escaped.replace(/`([^`]+)`/g,'<code>$1</code>');
  var lines = escaped.split('\n').map(function(l){return l.trim();}).filter(Boolean);
  var html = '';
  var inList = false;
  lines.forEach(function(l){
    if (/^[-*•]\s+/.test(l)) {
      if (!inList) { html += '<ul>'; inList = true; }
      html += '<li>' + l.replace(/^[-*•]\s+/, '') + '</li>';
    } else {
      if (inList) { html += '</ul>'; inList = false; }
      html += '<p>' + l + '</p>';
    }
  });
  if (inList) html += '</ul>';
  return html;
}

function planiShowTyping() {
  var container = document.getElementById('ccChatMessages');
  var welcome = document.getElementById('planiWelcome');
  if (welcome) welcome.style.display = 'none';
  var div = document.createElement('div');
  div.className = 'cc-msg cc-msg-bot cc-typing';
  div.id = 'ccChatTyping';
  div.innerHTML = '<span></span><span></span><span></span>';
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
function planiHideTyping() {
  var el = document.getElementById('ccChatTyping');
  if (el) el.remove();
}

/* Navigation is a proposal, not a command.

   This used to fire `window.location.href` on a 700ms timer as soon as
   the model asked for it — so the assistant could pull the user off the
   page mid-thought, and anything they had typed and not sent went with
   it. Leaving the Command Center is the single most disruptive thing
   this assistant can do, so it asks first. Declining is a real option
   and costs nothing: the answer is already in the thread. */
var CC_NAV_LABELS = {
  '/scheduler': 'Scheduler',
  '/dashboard': 'Dashboard',
  '/gradebook': 'Grade Modeler',
  '/grademodel': 'Grade Modeler',
  '/memories': 'Memories',
  '/streak': 'Streak',
  '/pet': 'My Pet',
  '/balance': 'Balance',
  '/study-and-learn': 'Study & Learn',
  '/my-stats': 'My Stats',
  '/settings': 'Settings'
};

function planiNavLabel(url) {
  var path = String(url || '').split('?')[0].replace(/\/$/, '') || '/';
  return CC_NAV_LABELS[path] || path;
}

function planiOfferNavigation(url) {
  if (!url) return;

  // Same-origin, app-relative only. A navigate directive is model output,
  // and model output must never be able to send the user off-site.
  var target;
  try {
    target = new URL(url, window.location.origin);
  } catch (e) { return; }
  if (target.origin !== window.location.origin) return;
  var href = target.pathname + target.search + target.hash;

  var stream = document.getElementById('ccChatMessages');
  if (!stream) return;

  var row = document.createElement('div');
  row.className = 'cc-chat-nav-offer';
  row.setAttribute('role', 'group');
  row.setAttribute('aria-label', 'Navigation suggestion');
  row.innerHTML =
    '<span class="cc-chat-nav-text">Open <strong></strong>?</span>' +
    '<button type="button" class="cc-chat-nav-go">Take me there</button>' +
    '<button type="button" class="cc-chat-nav-no">Stay here</button>';
  // textContent, not innerHTML — the label can be a raw path from the model.
  row.querySelector('strong').textContent = planiNavLabel(href);

  row.querySelector('.cc-chat-nav-go').addEventListener('click', function() {
    window.location.href = href;
  });
  row.querySelector('.cc-chat-nav-no').addEventListener('click', function() {
    row.remove();
  });

  stream.appendChild(row);
  stream.scrollTop = stream.scrollHeight;
}

function planiSend() {
  if (planiSending) return;
  var input = document.getElementById('ccChatInput');
  var msg = (input.value || '').trim();
  if (!msg) return;
  input.value = ''; planiAutosize(input);
  planiSending = true;
  planiUpdateSend();

  planiAddMsg('user', msg);
  ccChatHistory.push({ role: 'user', content: msg });
  planiShowTyping();

  fetch('/api/plani/agent', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages: ccChatHistory.slice(-10) })
  })
    .then(function(r) {
      var status = r.status;
      return r.json().then(function(data) { return { status: status, data: data }; });
    })
    .then(function(res) {
      planiHideTyping();
      var data = res.data;
      if (data.status === 'ok') {
        (data.actions || []).forEach(function(a) { planiAddMsg('action', a); });
        planiAddMsg('bot', data.reply || 'Done.');
        ccChatHistory.push({ role: 'assistant', content: data.reply || '' });
        if (data.refresh) {
          ccToast('Updated · refreshing data…', 'success');
          setTimeout(function() { ccBootstrap(); }, 400);
        }
        if (data.navigate) {
          planiOfferNavigation(data.navigate);
        }
        hydrateMomentum();
      } else if (res.status === 401) {
        planiAddMsg('bot', 'Please log in first.');
      } else {
        planiAddMsg('bot', data.reply || data.message || 'Something went wrong. Try again.');
      }
    })
    .catch(function() {
      planiHideTyping();
      planiAddMsg('bot', 'Network error. Try again.');
    })
    .finally(function() {
      planiSending = false;
      planiUpdateSend();
    });
}

/* ══════════════════════════════════════════════════════════════
   LEARNING INTELLIGENCE
═══════════════════════════════════════════════════════════════ */
function lgLoad() {
  fetch('/api/learning/dashboard', { credentials: 'same-origin' })
    .then(function(r) { if (!r.ok) throw r; return r.json(); })
    .then(function(d) { if (d.error) lgShowEmpty(); else lgRender(d); })
    .catch(function() { lgShowEmpty(); });
}

function lgShowEmpty() {
  var skel = document.getElementById('ccLearningSkeleton');
  var body = document.getElementById('ccLearningBody');
  var empty = document.getElementById('ccLgEmpty');
  if (skel) skel.hidden = true;
  if (body) body.hidden = false;
  if (empty) empty.hidden = false;
}

function lgRender(d) {
  var skel = document.getElementById('ccLearningSkeleton');
  var body = document.getElementById('ccLearningBody');
  if (skel) skel.hidden = true;
  if (body) body.hidden = false;
  var hasData = (d.strongest_concepts && d.strongest_concepts.length)
    || (d.weakest_concepts && d.weakest_concepts.length)
    || (d.predictions && d.predictions.length);
  if (!hasData) { lgShowEmpty(); return; }
  lgRenderAlerts(d.forgetting_soon || []);
  lgRenderPredictions(d.predictions || []);
  lgRenderConcepts('ccLgStrongest', d.strongest_concepts || []);
  lgRenderConcepts('ccLgWeakest', d.weakest_concepts || []);
  lgRenderHeatmap(d.mastery_by_subject || []);
  lgRenderSparkline('ccLgStudyTrend', d.study_trend || [], 'min');
  lgRenderSparkline('ccLgRetentionTrend', d.retention_trend || [], '%');
}

function lgRenderAlerts(items) {
  var wrap = document.getElementById('ccLgAlerts');
  var list = document.getElementById('ccLgAlertList');
  if (!wrap || !list || !items.length) return;
  wrap.hidden = false;
  list.innerHTML = items.map(function(c) {
    var risk = Math.round(c.forgetting_risk * 100);
    return '<div class="cc-lg-alert-chip">'
      + '<span class="cc-lg-alert-concept">' + escapeHtml(c.concept) + '</span>'
      + '<span class="cc-lg-alert-subject">' + escapeHtml(c.subject) + '</span>'
      + '<span class="cc-lg-alert-risk">' + risk + '% risk</span>'
      + '<span class="cc-lg-alert-days">' + c.days_since_review + 'd ago</span></div>';
  }).join('');
}

function lgRenderPredictions(preds) {
  var grid = document.getElementById('ccLgPredGrid');
  if (!grid || !preds.length) {
    var sec = document.getElementById('ccLgPredictions');
    if (sec) sec.hidden = true;
    return;
  }
  grid.innerHTML = preds.map(function(p) {
    var pct = typeof p.value === 'number' ? Math.round(p.value * 100) : '—';
    var tier = p.value >= 0.7 ? 'good' : p.value >= 0.4 ? 'mid' : 'risk';
    var conf = p.confidence_level || 'low';
    return '<div class="cc-lg-pred-card cc-lg-pred-' + tier + '">'
      + '<div class="cc-lg-pred-value">' + pct + '<small>%</small></div>'
      + '<div class="cc-lg-pred-label">' + escapeHtml(p.label) + '</div>'
      + '<div class="cc-lg-pred-conf">confidence: ' + conf + '</div>'
      + '<div class="cc-lg-pred-range">' + Math.round((p.confidence_low||0)*100) + '–' + Math.round((p.confidence_high||0)*100) + '%</div>'
      + (p.narrative ? '<p class="cc-lg-pred-narrative">' + escapeHtml(p.narrative) + '</p>' : '')
      + '</div>';
  }).join('');
}

function lgRenderConcepts(containerId, concepts) {
  var el = document.getElementById(containerId);
  if (!el) return;
  if (!concepts.length) { el.innerHTML = '<p class="cc-lg-no-data">No data yet</p>'; return; }
  el.innerHTML = concepts.map(function(c) {
    var pct = Math.round(c.mastery_score * 100);
    var hue = Math.round(c.mastery_score * 120);
    return '<div class="cc-lg-concept-row">'
      + '<div class="cc-lg-concept-bar-track"><div class="cc-lg-concept-bar-fill" style="transform:scaleX(' + (pct / 100) + ');background:hsl(' + hue + ',65%,48%)"></div></div>'
      + '<div class="cc-lg-concept-info"><span class="cc-lg-concept-name">' + escapeHtml(c.concept) + '</span><span class="cc-lg-concept-subject">' + escapeHtml(c.subject) + '</span></div>'
      + '<span class="cc-lg-concept-pct">' + pct + '%</span></div>';
  }).join('');
}

function lgRenderHeatmap(subjects) {
  var el = document.getElementById('ccLgHeatmap');
  if (!el || !subjects.length) {
    var sec = el ? el.closest('.cc-lg-heatmap-section') : null;
    if (sec) sec.hidden = true;
    return;
  }
  el.innerHTML = subjects.map(function(s) {
    var pct = Math.round((s.avg_mastery || 0) * 100);
    var hue = Math.round((s.avg_mastery || 0) * 120);
    return '<div class="cc-lg-heatmap-row">'
      + '<span class="cc-lg-heatmap-subject">' + escapeHtml(s.subject) + '</span>'
      + '<div class="cc-lg-heatmap-bar-track"><div class="cc-lg-heatmap-bar-fill" style="transform:scaleX(' + (pct / 100) + ');background:hsl(' + hue + ',65%,48%)"></div></div>'
      + '<span class="cc-lg-heatmap-pct">' + pct + '%</span>'
      + '<span class="cc-lg-heatmap-count">' + (s.concept_count || 0) + ' concepts</span></div>';
  }).join('');
}

function lgRenderSparkline(containerId, data, unit) {
  var el = document.getElementById(containerId);
  if (!el) return;
  if (!data.length) { el.innerHTML = '<p class="cc-lg-no-data">No data yet</p>'; return; }
  var max = Math.max.apply(null, data.map(function(v){ return v || 0; }));
  if (max === 0) max = 1;
  var w = 100 / data.length;
  var bars = data.map(function(v) {
    var h = Math.max(((v || 0) / max) * 100, 2);
    var val = unit === '%' ? Math.round((v||0)*100) + '%' : Math.round(v||0) + unit;
    return '<div class="cc-lg-spark-col" style="width:' + w + '%" title="' + val + '">'
      + '<div class="cc-lg-spark-bar" style="height:' + h + '%"></div></div>';
  }).join('');
  el.innerHTML = '<div class="cc-lg-spark-chart">' + bars + '</div>';
}

function lgSync() {
  var btn = document.getElementById('ccLearningSyncBtn');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  btn.classList.add('cc-spin-child');
  fetch('/api/learning/sync', { method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:'{}' })
    .then(function(r) {
      if (r.ok) { ccToast('Synced learning data', 'success'); lgLoad(); }
      else ccToast('Sync failed', 'error');
    })
    .catch(function() { ccToast('Network error', 'error'); })
    .finally(function() { btn.disabled = false; btn.classList.remove('cc-spin-child'); });
}
