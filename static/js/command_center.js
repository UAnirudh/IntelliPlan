/* Command Center chat. Talks to /api/plani/agent, the same agent as the
 * floating Plani, so every tool it has (tasks, schedules, calendar export,
 * notes, navigation) works here.
 *
 * The conversation is kept in sessionStorage so following a link Plani
 * offers and coming back does not wipe it. "New chat" clears it.
 */
(function () {
  'use strict';

  var STORE = 'ip_cc_thread_v1';
  var MAX_KEEP = 40;
  var thread, inner, input, send;
  var history = [];      // {role, content} sent to the agent
  var log = [];          // rendered items, persisted: {t:'msg'|'action'|'schedule', ...}
  var sending = false;

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Minimal, safe formatting: **bold**, `code`, line breaks, and in-app
  // links like /scheduler. Everything is escaped first.
  function format(text) {
    return esc(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/(^|\s)(\/[a-z][a-z0-9\-\/]*)(?=[\s.,;:!?)]|$)/g, '$1<a href="$2">$2</a>')
      .replace(/\n/g, '<br>');
  }

  function save() {
    try {
      sessionStorage.setItem(STORE, JSON.stringify({
        history: history.slice(-MAX_KEEP), log: log.slice(-MAX_KEEP)
      }));
    } catch (e) { /* storage full or blocked: session-only */ }
  }

  function scrollDown() { thread.scrollTop = thread.scrollHeight; }

  function dropIntro() {
    var intro = document.getElementById('chatIntro');
    if (intro) intro.remove();
  }

  function renderMsg(role, text) {
    var el = document.createElement('article');
    el.className = 'command-chat__message command-chat__message--' + role;
    el.innerHTML = role === 'assistant' ? format(text) : esc(text).replace(/\n/g, '<br>');
    inner.appendChild(el);
  }

  function renderAction(text) {
    var el = document.createElement('p');
    var clean = String(text || '').replace(/^[✓→]\s*/, '');
    el.className = 'command-chat__action' + (/no calendar connected|failed|couldn/i.test(clean) ? ' command-chat__action--warn' : '');
    el.textContent = clean;
    inner.appendChild(el);
  }

  function renderSchedule(schedule) {
    if (!schedule || !Array.isArray(schedule.schedule)) return;
    var days = schedule.schedule.slice(0, 7).map(function (day) {
      var blocks = (day.blocks || []).filter(function (b) { return !b.is_break; }).map(function (b) {
        return '<li><time>' + esc(String(b.time_slot || '').split(/\s*[-–]\s*/)[0]) + '</time><span>' + esc(b.assignment || b.title) + '</span></li>';
      }).join('');
      return '<div><h3>' + esc(day.day_name || day.date) + '</h3><ul>' + (blocks || '<li>Free</li>') + '</ul></div>';
    }).join('');
    var card = document.createElement('section');
    card.className = 'command-chat__schedule';
    card.innerHTML = '<header><div><p>New schedule</p><h2>' + esc(schedule.total_study_time || 'Study plan') +
      '</h2></div><a href="/scheduler">Open in Scheduler</a></header><div class="command-chat__schedule-days">' + days + '</div>';
    inner.appendChild(card);
  }

  function push(item) {
    log.push(item);
    if (item.t === 'msg') renderMsg(item.role, item.text);
    else if (item.t === 'action') renderAction(item.text);
    else if (item.t === 'schedule') renderSchedule(item.schedule);
  }

  function typing(show) {
    var old = document.getElementById('chatTyping');
    if (old) old.remove();
    if (!show) return;
    var el = document.createElement('div');
    el.id = 'chatTyping';
    el.className = 'command-chat__typing';
    el.setAttribute('aria-label', 'Plani is working');
    el.innerHTML = '<span></span><span></span><span></span>';
    inner.appendChild(el);
    scrollDown();
  }

  function update() { send.disabled = sending || !input.value.trim(); }

  function submit(text) {
    var content = String(text || input.value).trim();
    if (!content || sending) return;
    input.value = '';
    input.style.height = 'auto';
    sending = true;
    update();
    dropIntro();
    push({ t: 'msg', role: 'user', text: content });
    history.push({ role: 'user', content: content });
    save();
    typing(true);

    fetch('/api/plani/agent', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: history.slice(-12) })
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) { return { status: r.status, data: data }; });
      })
      .then(function (res) {
        typing(false);
        var data = res.data || {};
        if (res.status === 401) {
          push({ t: 'msg', role: 'assistant', text: 'Sign in so I can work with your tasks and calendar: /login' });
          return;
        }
        (data.actions || []).forEach(function (a) { push({ t: 'action', text: a }); });
        var reply = data.reply || data.message || 'I could not complete that. Try again.';
        push({ t: 'msg', role: 'assistant', text: reply });
        if (data.status === 'ok') history.push({ role: 'assistant', content: reply });
        if (data.schedule) push({ t: 'schedule', schedule: data.schedule });
        save();
        // Plani's navigate_to tool: go where it said, after the reply has
        // been read. The conversation is saved, so coming back restores it.
        if (data.navigate && /^\/[a-z0-9\-\/]*$/i.test(data.navigate)) {
          setTimeout(function () { location.href = data.navigate; }, 1200);
        }
      })
      .catch(function () {
        typing(false);
        push({ t: 'msg', role: 'assistant', text: 'I could not reach IntelliPlan. Check your connection and try again.' });
        save();
      })
      .finally(function () {
        sending = false;
        update();
        scrollDown();
        input.focus();
      });
    scrollDown();
  }

  function restore() {
    try {
      var saved = JSON.parse(sessionStorage.getItem(STORE) || 'null');
      if (!saved || !Array.isArray(saved.log) || !saved.log.length) return;
      history = Array.isArray(saved.history) ? saved.history : [];
      dropIntro();
      saved.log.forEach(push);
      scrollDown();
    } catch (e) { /* corrupt entry: start fresh */ }
  }

  function measurePhoneTop() {
    var header = document.querySelector('body > header');
    var h = header && window.matchMedia('(max-width: 768px)').matches
      ? Math.max(0, header.getBoundingClientRect().bottom) : 0;
    document.documentElement.style.setProperty('--cc-top', h + 'px');
  }

  function showCalendarState() {
    fetch('/calendar/connections', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var state = document.getElementById('calendarState');
        var names = [];
        if (data.google) names.push('Google Calendar');
        if (data.outlook) names.push('Outlook');
        if (state) {
          state.textContent = names.length
            ? 'Schedules Plani makes are added to ' + names.join(' and ') + '.'
            : 'No calendar connected. Schedules stay in IntelliPlan until you connect one in Settings.';
        }
        var link = document.getElementById('outlookConnect');
        if (link && data.outlook_configured && !data.outlook) link.hidden = false;
      })
      .catch(function () {});
  }

  document.addEventListener('DOMContentLoaded', function () {
    thread = document.getElementById('chatThread');
    inner = document.getElementById('chatInner');
    input = document.getElementById('chatInput');
    send = document.getElementById('sendMessage');
    if (!thread || !inner || !input || !send) return;

    document.getElementById('chatComposer').addEventListener('submit', function (e) {
      e.preventDefault();
      submit();
    });
    input.addEventListener('input', function () {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 180) + 'px';
      update();
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        submit();
      }
    });
    inner.addEventListener('click', function (e) {
      var b = e.target.closest('[data-prompt]');
      if (b) submit(b.dataset.prompt);
    });
    document.getElementById('newConversation').addEventListener('click', function () {
      try { sessionStorage.removeItem(STORE); } catch (e) {}
      location.reload();
    });

    measurePhoneTop();
    window.addEventListener('resize', measurePhoneTop);
    restore();
    showCalendarState();
    input.focus();
  });
})();
