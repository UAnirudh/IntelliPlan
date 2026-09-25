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
        '<button type="button" class="ipbd-x" data-ipbd-close aria-label="Close">&times;</button>' +
        '<div class="ipbd-head">' +
          '<h2 id="ipBlockTitle" class="ipbd-title"></h2>' +
          '<div class="ipbd-meta"></div>' +
        '</div>' +
        '<div class="ipbd-notes"></div>' +
        '<div class="ipbd-section">' +
          '<h3 class="ipbd-h3">Resources</h3>' +
          '<div class="ipbd-resources"><p class="ipbd-muted">Finding resources…</p></div>' +
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
    host.innerHTML = '<p class="ipbd-muted">Finding resources…</p>';

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

    var title = block.assignment || block.title || 'Study block';
    el.querySelector('.ipbd-title').textContent = block.is_break ? 'Break' : title;

    var bits = [];
    if (block.course) bits.push(esc(block.course));
    if (block.time_slot) bits.push(esc(block.time_slot));
    if (block.duration_minutes) bits.push(esc(String(block.duration_minutes)) + ' min');
    if (block.due_date) bits.push('due ' + esc(block.due_date));
    el.querySelector('.ipbd-meta').innerHTML =
      bits.map(function (b) { return '<span class="ipbd-pill">' + b + '</span>'; }).join('');

    var notes = el.querySelector('.ipbd-notes');
    var text = block.notes || block.why_now || '';
    notes.innerHTML = text ? '<p>' + esc(text) + '</p>' : '';

    var section = el.querySelector('.ipbd-section');
    // A break is the one block where offering study material is actively
    // wrong, so the section goes rather than showing an empty state.
    section.hidden = !!block.is_break;

    el.hidden = false;
    document.body.style.overflow = 'hidden';
    el.querySelector('.ipbd-x').focus();

    if (!block.is_break) loadResources(block, ++requestSeq);
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
    var block;
    try {
      block = JSON.parse(host.getAttribute('data-ip-block'));
    } catch (e) {
      return;
    }
    open(block);
  });

  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') close();
  });

  window.IPBlock = { open: open, close: close, current: function () { return current; } };
})();
