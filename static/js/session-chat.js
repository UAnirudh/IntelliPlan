/** Shared chat panel for live sessions and study groups.
 *
 * Mounted by groups.html (once per group the student opens) and by
 * live_session.html. groups.html rebuilds its whole panel on every
 * `openGroup()`, so this function is called repeatedly with a *new* host
 * element each time and the previous mount is left detached — which is why
 * the poll below is tracked in a module-level registry rather than a local
 * variable. A timer belonging to a detached mount is unreachable from the
 * page and would otherwise keep polling that group's endpoint until the tab
 * closed.
 */
(function () {
  'use strict';

  var POLL_MS = 8000;

  // Every live mount, so a re-mount can stop the one it replaces. Entries are
  // { host, timer }; a host no longer in the document is a dead mount.
  var mounts = [];

  function stopStaleMounts(newHost) {
    mounts = mounts.filter(function (m) {
      if (m.host === newHost || !document.contains(m.host)) {
        if (m.stop) m.stop();
        clearInterval(m.timer);
        document.removeEventListener('visibilitychange', m.onVisible);
        return false;
      }
      return true;
    });
  }

  function warn(message) {
    // ip-core.js owns user-facing messages and says, at the top of the file,
    // never window.alert. A blocking dialog in a chat panel also freezes the
    // poll behind it until the student dismisses it.
    if (window.IP && typeof window.IP.alert === 'function') window.IP.alert(message, 'error');
    else if (window.IP && typeof window.IP.toast === 'function') window.IP.toast(message, 'error');
  }

  window.initSessionChat = function initSessionChat(opts) {
    var host = document.getElementById(opts.hostId);
    if (!host) return;

    stopStaleMounts(host);

    var ctxType = opts.contextType;
    var ctxId = opts.contextId;
    var canSave = !!opts.canSave;

    host.innerHTML =
      '<button type="button" class="btn-secondary" id="sessChatOlder" hidden>Load earlier messages</button>' +
      '<div class="sess-chat-log" id="sessChatLog" role="log" aria-live="polite"></div>' +
      '<div class="sess-chat-compose">' +
        '<textarea id="sessChatInput" rows="2" placeholder="Share a link, formula, or note…" maxlength="8000"></textarea>' +
        '<div class="sess-chat-actions">' +
          '<button type="button" class="btn-primary" id="sessChatSend" style="padding:8px 14px;border:none;cursor:pointer;font-size:0.84rem;">Send</button>' +
          '<span id="sessChatStatus" style="font-size:0.76rem;color:var(--text-muted);"></span>' +
        '</div>' +
      '</div>';

    var log = host.querySelector('#sessChatLog');
    var input = host.querySelector('#sessChatInput');
    var status = host.querySelector('#sessChatStatus');
    var sendBtn = host.querySelector('#sessChatSend');
    var olderBtn = host.querySelector('#sessChatOlder');

    var lastSignature = null;   // what the log is currently showing
    var sending = false;
    var messages = [];
    var cursor = 0;
    var initialized = false;
    var hasOlder = false;
    var inFlight = null;
    var stopped = false;

    function esc(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    /** Cheap identity for a rendered list: changes only when the view should. */
    function signature(messages) {
      return messages.map(function (m) {
        return [m.id, m.saved_to_library ? 1 : 0, m.body, m.author_name, m.created_at].join('');
      }).join('');
    }

    /** Is the student parked at the bottom, or reading back through history? */
    function atBottom() {
      return log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    }

    function render(messages, opts2) {
      var sig = signature(messages);
      // Re-assigning innerHTML every 8 seconds destroyed any text selection
      // in the log and, with the unconditional scroll below, yanked a student
      // reading history back to the bottom on every poll. Nothing changed
      // means nothing to redraw.
      if (sig === lastSignature) return;
      var wasAtBottom = lastSignature === null || atBottom();
      var oldHeight = log.scrollHeight, oldTop = log.scrollTop;
      lastSignature = sig;

      if (!messages.length) {
        log.innerHTML = '<div class="sess-chat-empty">No messages yet — share materials with the room.</div>';
        return;
      }

      log.innerHTML = messages.map(function (m) {
        var time = m.created_at
          ? new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
          : '';
        return '<div class="sess-chat-msg" data-id="' + esc(m.id) + '">' +
            '<div class="sess-chat-meta"><strong>' + esc(m.author_name) + '</strong> · ' + esc(time) + '</div>' +
            '<div class="sess-chat-body">' + esc(m.body).replace(/\n/g, '<br>') + '</div>' +
            (canSave
              ? '<button type="button" class="sess-chat-save" data-save="' + esc(m.id) + '"' +
                (m.saved_to_library ? ' disabled' : '') + '>' +
                (m.saved_to_library ? 'Saved' : 'Save to notes') + '</button>'
              : '') +
          '</div>';
      }).join('');

      // Follow the conversation only for someone already at the bottom, or
      // when they just sent something themselves.
      if (opts2 && opts2.older) log.scrollTop = oldTop + log.scrollHeight - oldHeight;
      else if (wasAtBottom || (opts2 && opts2.forceScroll)) log.scrollTop = log.scrollHeight;

      log.querySelectorAll('[data-save]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          saveMsg(parseInt(btn.dataset.save, 10), btn);
        });
      });
    }

    function merge(incoming) {
      var byId = new Map();
      messages.concat(incoming).forEach(function (message) {
        if (message && Number.isSafeInteger(message.id) && message.id > 0) byId.set(message.id, message);
      });
      messages = Array.from(byId.values()).sort(function (a, b) { return a.id - b.id; });
    }

    function load(opts2) {
      opts2 = opts2 || {};
      if (stopped || (document.hidden && !opts2.forceScroll && !opts2.older)) return Promise.resolve();
      // A slow request must not overlap the next poll or race its cursor.
      if (inFlight) return inFlight;
      if (olderBtn) olderBtn.disabled = true;
      var older = !!opts2.older;
      function page(number) {
        var query = older && messages.length ? '?before_id=' + messages[0].id
          : initialized ? '?after_id=' + cursor : '';
        return fetch('/api/sessions/' + ctxType + '/' + ctxId + '/messages' + query, {
          credentials: 'same-origin'
        }).then(function (r) {
          if (r.ok === false) throw new Error('Chat request failed');
          return r.json();
        }).then(function (d) {
          if (stopped) return;
          if (!d || d.status !== 'ok' || !Array.isArray(d.messages)) throw new Error('Invalid chat response');
          var cursorPage = Object.prototype.hasOwnProperty.call(d, 'next_after_id');
          var lower = older || !cursorPage ? 0 : initialized ? cursor : 0;
          var previous = lower;
          d.messages.forEach(function (message) {
            if (!message || !Number.isSafeInteger(message.id) || message.id <= previous ||
                (older && messages.length && message.id >= messages[0].id)) throw new Error('Invalid chat page');
            previous = message.id;
          });
          var next = Number.isSafeInteger(d.next_after_id) ? d.next_after_id
            : d.messages.reduce(function (max, m) { return Math.max(max, Number(m.id) || 0); }, cursor);
          if (cursorPage && (!Number.isSafeInteger(d.next_after_id) || d.next_after_id < 0)) throw new Error('Invalid chat cursor');
          if (!older && next !== (d.messages.length ? previous : cursor)) throw new Error('Invalid chat cursor');
          merge(d.messages);
          if (!initialized || older) hasOlder = d.has_more_before === true;
          if (!older) { cursor = next; initialized = true; }
          if (olderBtn) olderBtn.hidden = !hasOlder;
          render(messages, opts2);
          // Catch up after an offline period in bounded pages, then let the
          // next poll continue from the last successfully accepted page.
          if (!older && d.has_more === true && number < 4 && d.messages.length && !document.hidden) {
            return page(number + 1);
          }
        });
      }
      inFlight = page(1)
        .catch(function () {
          if (older) warn('Earlier messages could not be loaded. Please try again.');
          // A failed response never advances the cursor or discards history.
        }).then(function () {
          inFlight = null;
          if (olderBtn) olderBtn.disabled = false;
        });
      return inFlight;
    }

    function send() {
      if (sending) return;               // Enter twice used to post twice
      var body = input.value.trim();
      if (!body) return;
      sending = true;
      sendBtn.disabled = true;
      status.textContent = 'Sending…';
      fetch('/api/sessions/' + ctxType + '/' + ctxId + '/messages', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body: body })
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.status === 'ok') {
            input.value = '';
            status.textContent = '';
            // Show our own confirmed post promptly without jumping the poll
            // cursor over messages sent concurrently by another participant.
            if (d.message) { merge([d.message]); render(messages, { forceScroll: true }); }
            return load({ forceScroll: true });
          }
          status.textContent = d.message || 'Send failed';
        })
        .catch(function () { status.textContent = 'Network error'; })
        .then(function () { sending = false; sendBtn.disabled = false; });
    }

    function saveMsg(id, btn) {
      btn.disabled = true;
      fetch('/api/sessions/messages/' + id + '/save', {
        method: 'POST', credentials: 'same-origin'
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.status === 'ok') {
            btn.textContent = 'Saved';
            messages.forEach(function (message) { if (message.id === id) message.saved_to_library = true; });
            lastSignature = signature(messages);
          } else {
            btn.disabled = false;
            warn(d.message || 'Could not save');
          }
        })
        .catch(function () { btn.disabled = false; });
    }

    sendBtn.addEventListener('click', send);
    if (olderBtn) olderBtn.addEventListener('click', function () { load({ older: true }); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });

    load();
    // Polling a backgrounded tab spends the student's battery and our request
    // budget on a panel nobody is looking at; the visibilitychange below
    // catches them up the moment they return.
    var timer = setInterval(function () {
      if (!document.hidden) load();
    }, POLL_MS);

    // Registered alongside the timer so stopStaleMounts can unhook it too --
    // a listener kept per mount is the same leak the registry exists to stop.
    function onVisible() {
      if (!document.hidden) load();
    }
    document.addEventListener('visibilitychange', onVisible);

    mounts.push({ host: host, timer: timer, onVisible: onVisible, stop: function () { stopped = true; } });
  };
})();
