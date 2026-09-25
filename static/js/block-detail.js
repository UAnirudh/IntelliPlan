/* Block detail panel — the same one on the scheduler, Active study and
   today's plan.

   Tapping a block should tell you more than its title did, and hand you
   something to open. Three surfaces show the same blocks, so this is one
   component they all mount rather than three that drift apart the way the
   integrations list did.

   Resources come from /api/block/resources. The model there picks a
   provider and search terms; the server builds the URL. Nothing this file
   renders as a link was authored by a model, which is what makes a dead
   link structurally impossible rather than merely unlikely. */
(function () {
  'use strict';

  var PANEL_ID = 'ipBlockPanel';
  var current = null;      // the block the panel is showing
  var requestSeq = 0;      // guards against a slow response for an old block
  var opener = null;       // element to hand focus back to on close
  var SKELETON = '<div class="ipbd-skel"></div><div class="ipbd-skel"></div>';
  var PART_RE = /\s*\((?:part\s+)?(\d+)\s+of\s+(\d+)\)\s*$/i;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* A resource URL reaches us as an anchor href. Everything the server
     returns is built from a provider template or came from the student's
     own coursework, but rendering an href without checking the scheme is
     exactly how a stored javascript: URL becomes XSS, so check here too --
     the cost is one comparison and the failure mode is silent. */
  function safeHref(url) {
    var u = String(url || '').trim();
    return /^https?:\/\//i.test(u) ? u : '';
  }

  function panel() {
    var el = document.getElementById(PANEL_ID);
    if (el) return el;

    el = document.createElement('div');
    el.id = PANEL_ID;
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-labelledby', 'ipBlockTitle');
    el.hidden = true;
    el.innerHTML =
      '<div class="ipbd-scrim" data-ipbd-close></div>' +
      '<div class="ipbd-sheet">' +
        '<button type="button" class="ipbd-x" data-ipbd-close aria-label="Close">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>' +
        '</button>' +
        '<div class="ipbd-head">' +
          '<div class="ipbd-eyebrow"></div>' +
          '<h2 id="ipBlockTitle" class="ipbd-title"></h2>' +
          '<dl class="ipbd-facts"></dl>' +
        '</div>' +
        '<div class="ipbd-notes"></div>' +
        '<div class="ipbd-section">' +
          '<h3 class="ipbd-h3">Resources</h3>' +
          '<div class="ipbd-resources" aria-live="polite">' + SKELETON + '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(el);

    el.addEventListener('click', function (ev) {
      if (ev.target.closest('[data-ipbd-close]')) close();
    });
    return el;
  }

  function close() {
    var el = document.getElementById(PANEL_ID);
    if (!el) return;
    el.hidden = true;
    current = null;
    // A panel left open counts as open forever if the seq is not moved on,
    // so a response still in flight cannot paint into the next block.
    requestSeq++;
    document.body.style.overflow = '';
    if (opener && document.contains(opener)) opener.focus();
    opener = null;
  }

  function resourceRow(r) {
    var href = safeHref(r.url);
    if (!href) return '';
    var why = r.why ? '<div class="ipbd-why">' + esc(r.why) + '</div>' : '';
    var mine = (r.source === 'linked_account' || r.source === 'assignment');
    return '' +
      '<a class="ipbd-res' + (mine ? ' ipbd-res--mine' : '') + '" href="' + esc(href) + '"' +
        ' target="_blank" rel="noopener noreferrer">' +
        '<div class="ipbd-res-top">' +
          '<span class="ipbd-res-title">' + esc(r.title) + '</span>' +
          '<span class="ipbd-res-tag">' + esc(r.provider) + '</span>' +
        '</div>' + why +
      '</a>';
  }

  function renderResources(rows) {
    var host = panel().querySelector('.ipbd-resources');
    var html = (rows || []).map(resourceRow).join('');
    host.innerHTML = html ||
      '<p class="ipbd-muted">Nothing to suggest for this one. ' +
      '<a href="/settings#resources">Link your own material</a> and it will show up here.</p>';
  }

  function loadResources(block, seq) {
    var host = panel().querySelector('.ipbd-resources');
    host.innerHTML = SKELETON;

    fetch('/api/block/resources', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ block: block })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        // A student can close one block and open another while this is in
        // flight. Without the check the first block's resources paint into
        // the second, which looks like the feature recommending nonsense.
        if (seq !== requestSeq) return;
        renderResources(data && data.resources);
      })
      .catch(function () {
        if (seq !== requestSeq) return;
        host.innerHTML = '<p class="ipbd-muted">Could not load resources just now.</p>';
      });
  }

  function open(block) {
    if (!block || typeof block !== 'object') return;
    var el = panel();
    current = block;

    var title = String(block.assignment || block.title || 'Study block');
    var part = PART_RE.exec(title);
    var titleEl = el.querySelector('.ipbd-title');
    if (block.is_break) {
      titleEl.textContent = 'Break';
    } else {
      titleEl.innerHTML = esc(part ? title.replace(PART_RE, '') : title) +
        (part ? '<span class="ipbd-part">Session ' + esc(part[1]) + ' of ' + esc(part[2]) + '</span>' : '');
    }

    var sheet = el.querySelector('.ipbd-sheet');
    // Only a plain colour value may reach a style property.
    var color = /^#[0-9a-f]{3,8}$|^(rgb|hsl)a?\([\d\s.,%]+\)$/i.test(String(block.color || '')) ? block.color : '';
    if (color) sheet.style.setProperty('--subj', color);
    else sheet.style.removeProperty('--subj');
    el.querySelector('.ipbd-eyebrow').textContent = block.is_break ? '' : (block.course || '');

    var facts = [];
    if (block.time_slot) facts.push(['When', block.time_slot]);
    if (block.duration_minutes) facts.push(['Length', fmtMinutes(block.duration_minutes)]);
    if (block.due_date) facts.push(['Due', fmtDue(block.due_date), isPast(block.due_date)]);
    el.querySelector('.ipbd-facts').innerHTML = facts.map(function (f) {
      return '<div class="ipbd-fact' + (f[2] ? ' ipbd-fact--late' : '') + '"><dt>' + esc(f[0]) + '</dt><dd>' + esc(f[1]) + '</dd></div>';
    }).join('');

    var notes = el.querySelector('.ipbd-notes');
    var text = block.notes || block.why_now || '';
    notes.innerHTML = text ? '<p>' + esc(text) + '</p>' : '';

    var section = el.querySelector('.ipbd-section');
    // A break is the one block where offering study material is actively
    // wrong, so the section goes rather than showing an empty state.
    section.hidden = !!block.is_break;

    if (el.hidden) opener = document.activeElement;
    el.hidden = false;
    document.body.style.overflow = 'hidden';
    el.querySelector('.ipbd-x').focus();

    if (!block.is_break) loadResources(block, ++requestSeq);
  }

  function fmtMinutes(min) {
    var n = Math.max(0, Math.round(Number(min) || 0));
    if (n < 60) return n + ' min';
    var h = Math.floor(n / 60), r = n % 60;
    return r ? h + 'h ' + r + 'm' : h + 'h';
  }

  function parseDay(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }

  function fmtDue(iso) {
    var d = parseDay(iso);
    if (!d) return String(iso);
    var today = new Date(); today.setHours(0, 0, 0, 0);
    var diff = Math.round((d - today) / 86400000);
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Tomorrow';
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function isPast(iso) {
    var d = parseDay(iso);
    var today = new Date(); today.setHours(0, 0, 0, 0);
    return !!d && d < today;
  }

  function blockFrom(host) {
    try { return JSON.parse(host.getAttribute('data-ip-block')); } catch (e) { return null; }
  }

  /* Delegation rather than a handler per rendered block: every surface
     re-renders its list from innerHTML, which would drop per-element
     listeners on each repaint. A surface opts in by putting the block's
     JSON on the element as data-ip-block. */
  document.addEventListener('click', function (ev) {
    var host = ev.target.closest('[data-ip-block]');
    if (!host) return;
    // Let a real control inside the card do its own job.
    if (ev.target.closest('a, button, input, select, textarea')) return;
    open(blockFrom(host));
  });

  document.addEventListener('keydown', function (ev) {
    var el = document.getElementById(PANEL_ID);
    var isOpen = el && !el.hidden;
    if (ev.key === 'Escape' && isOpen) { close(); return; }
    // Rows are role="button"; honour the keys a button answers to.
    if (!isOpen && (ev.key === 'Enter' || ev.key === ' ')) {
      var host = ev.target.closest && ev.target.closest('[data-ip-block]');
      if (host && host === ev.target) { ev.preventDefault(); open(blockFrom(host)); }
      return;
    }
    // Keep Tab inside the dialog while it is open.
    if (isOpen && ev.key === 'Tab') {
      var f = el.querySelectorAll('.ipbd-sheet a[href], .ipbd-sheet button');
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    }
  });

  window.IPBlock = { open: open, close: close, current: function () { return current; } };
})();
