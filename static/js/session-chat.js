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

    var lastSignature = null;   // what the log is currently showing
    var sending = false;

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
      if (wasAtBottom || (opts2 && opts2.forceScroll)) log.scrollTop = log.scrollHeight;

      log.querySelectorAll('[data-save]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          saveMsg(parseInt(btn.dataset.save, 10), btn);
        });
      });
    }

    function load(opts2) {
      return fetch('/api/sessions/' + ctxType + '/' + ctxId + '/messages', {
        credentials: 'same-origin'
      })
        .then(function (r) { return r.json(); })
        .then(function (d) { if (d.status === 'ok') render(d.messages || [], opts2); })
        .catch(function () { /* a dropped poll is not worth interrupting for */ });
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
            // The next poll would otherwise see the same signature it already
            // has and skip the redraw, leaving the button re-enabled.
            lastSignature = null;
          } else {
            btn.disabled = false;
            warn(d.message || 'Could not save');
          }
        })
        .catch(function () { btn.disabled = false; });
    }

    sendBtn.addEventListener('click', send);
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

    mounts.push({ host: host, timer: timer, onVisible: onVisible });
  };
})();
