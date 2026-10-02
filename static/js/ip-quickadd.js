/* Quick add — capture a task from anywhere in the web app.
 *
 *   q            open the palette in quick-add mode (when not typing)
 *   Cmd/Ctrl+K   the palette; any text you type can be added as a task
 *   ?quickadd=1  opens quick add on load (used by the desktop tray)
 *
 * The parse happens on the server (/api/quick-add), deterministically and
 * without AI, so "quiz fri" means the same date here as in the extension or
 * the phone. While typing, a debounced preview shows what was understood;
 * after Enter, the reply says where the task landed in the plan.
 */
(function () {
  'use strict';

  var PREVIEW_DELAY = 180;
  var timer = null;
  var lastPreview = '';
  var busy = false;

  function box() { return document.getElementById('cmdQuick'); }

  function tz() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { return ''; }
  }

  function chip(text) {
    var span = document.createElement('span');
    span.className = 'cmd-quick__chip';
    span.textContent = text;
    return span;
  }

  function fmtMinutes(m) {
    if (!m) return '';
    if (m < 60) return m + 'm';
    return Math.floor(m / 60) + 'h' + (m % 60 ? ' ' + (m % 60) + 'm' : '');
  }

  function fmtDate(iso) {
    if (!iso) return '';
    var d = new Date(iso + 'T12:00:00');
    if (isNaN(d)) return iso;
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function showParsed(data) {
    var el = box();
    if (!el) return;
    el.textContent = '';
    if (!data || !data.parsed) { el.hidden = true; return; }
    var p = data.parsed;
    el.appendChild(chip(p.title || 'New task'));
    if (p.due_date) el.appendChild(chip('Due ' + fmtDate(p.due_date) + (p.due_time ? ' ' + p.due_time : '')));
    if (data.minutes) el.appendChild(chip(fmtMinutes(data.minutes) + (data.minutes_source === 'estimated' ? ' (est.)' : '')));
    if (p.course) el.appendChild(chip(p.course));
    if (p.priority) el.appendChild(chip(p.priority + ' priority'));
    el.removeAttribute('data-state');
    el.hidden = false;
  }

  function showMessage(text, state) {
    var el = box();
    if (!el) return;
    el.textContent = text;
    el.setAttribute('data-state', state || '');
    el.hidden = !text;
  }

  function post(path, body) {
    return fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.message || 'Could not add that.');
        return data;
      });
    });
  }

  function preview(text) {
    text = (text || '').trim();
    window.clearTimeout(timer);
    if (busy) return;
    if (!text) { lastPreview = ''; showMessage('', ''); return; }
    if (text === lastPreview) return;
    timer = window.setTimeout(function () {
      lastPreview = text;
      post('/api/quick-add/preview', { text: text, timezone: tz() })
        .then(function (data) { if (lastPreview === text) showParsed(data); })
        .catch(function () { /* the preview is a nicety; Enter still works */ });
    }, PREVIEW_DELAY);
  }

  function submit(text) {
    text = (text || '').trim();
    if (!text || busy) return Promise.resolve(null);
    busy = true;
    window.clearTimeout(timer);
    showMessage('Adding…', 'busy');
    return post('/api/quick-add', { text: text, timezone: tz() })
      .then(function (data) {
        showMessage(data.message || 'Added.', 'ok');
        var input = document.getElementById('cmdInput');
        if (input) { input.value = ''; input.focus(); }
        lastPreview = '';
        // Pages that list tasks can refresh themselves.
        try { document.dispatchEvent(new CustomEvent('ip:task-added', { detail: data })); } catch (e) {}
        return data;
      })
      .catch(function (err) {
        showMessage(err.message || 'Could not add that.', 'error');
        return null;
      })
      .then(function (data) { busy = false; return data; });
  }

  function editable(el) {
    if (!el) return false;
    var tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
  }

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'q' || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
    if (editable(e.target) || editable(document.activeElement)) return;
    if (!window.openCommandPalette) return;
    e.preventDefault();
    window.openCommandPalette('quick');
  });

  document.addEventListener('DOMContentLoaded', function () {
    try {
      var params = new URLSearchParams(window.location.search);
      if (params.get('quickadd') === '1' && window.openCommandPalette) {
        window.openCommandPalette('quick');
      }
    } catch (e) {}
  });

  window.IPQuickAdd = {
    preview: preview,
    submit: submit,
    open: function () { if (window.openCommandPalette) window.openCommandPalette('quick'); }
  };
})();
