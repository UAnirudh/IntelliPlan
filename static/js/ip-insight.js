/* In-app questions, and the client half of consented usage events.
 *
 * Two small things:
 *   IP.insight.track(name, props)  -- fire-and-forget; the server decides
 *                                    whether consent allows recording, and
 *                                    answers 204 either way so this file
 *                                    never learns the visitor's choice.
 *   the prompt card                -- asks the one question the server says
 *                                    is due, at most one a day, never for a
 *                                    child, and never on a study surface.
 *
 * The card is deliberately late and quiet: a survey that interrupts someone
 * mid-session is the thing standing between them and their homework.
 */
(function () {
  'use strict';
  if (window.__ipInsightInstalled) return;
  window.__ipInsightInstalled = true;

  var DELAY_MS = 12000;
  var SESSION_KEY = 'ip_prompt_seen_session';
  // Anywhere a student is actually working. Asking here is an interruption,
  // not research.
  var QUIET_PATHS = ['/active', '/study', '/focus', '/live', '/tutor', '/onboarding', '/foundations'];

  var IP = (window.IP = window.IP || {});

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }

  IP.insight = {
    track: function (name, props) {
      if (location.pathname.indexOf('/foundations') === 0) return;
      try {
        post('/api/insight/event', { name: name, rule: null, props: props || {} }).catch(function () {});
      } catch (e) { /* never break a caller */ }
    },
  };

  function quiet() {
    var path = location.pathname;
    for (var i = 0; i < QUIET_PATHS.length; i++) {
      if (path.indexOf(QUIET_PATHS[i]) === 0) return true;
    }
    return false;
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function render(prompt) {
    var card = el('section', 'ip-prompt');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-label', prompt.question);

    var close = el('button', 'ip-prompt-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Dismiss');
    card.appendChild(close);
    card.appendChild(el('h2', 'ip-prompt-q', prompt.question));

    var detail = null;
    function finish(answer, dismissed) {
      post('/api/insight/prompt', {
        key: prompt.key,
        answer: answer,
        dismissed: !!dismissed,
        detail: detail && detail.value ? detail.value.slice(0, 500) : '',
      }).catch(function () {});
      IP.insight.track(dismissed ? 'prompt_dismissed' : 'prompt_answered', { step: prompt.key });
      card.classList.add('is-done');
      if (!dismissed) {
        card.innerHTML = '';
        card.appendChild(el('p', 'ip-prompt-thanks', 'Thank you — that genuinely helps.'));
        setTimeout(function () { card.remove(); }, 2600);
      } else {
        card.remove();
      }
    }

    close.addEventListener('click', function () { finish(null, true); });

    if (prompt.options && prompt.options.length) {
      var list = el('div', 'ip-prompt-options');
      prompt.options.forEach(function (opt) {
        var button = el('button', 'ip-prompt-option', opt.label);
        button.type = 'button';
        button.addEventListener('click', function () {
          if (prompt.key === 'invite' && opt.value === 'copied' && prompt.invite_url) {
            copy(prompt.invite_url, button);
            IP.insight.track('invite_link_copied', { surface: 'prompt' });
            finish('copied', false);
            return;
          }
          if (detail) { detail.hidden = false; detail.focus(); }
          finish(opt.value, false);
        });
        list.appendChild(button);
      });
      card.appendChild(list);
    }

    if (prompt.detail_label) {
      detail = el('textarea', 'ip-prompt-detail');
      detail.rows = 2;
      detail.placeholder = prompt.detail_label;
      detail.setAttribute('aria-label', prompt.detail_label);
      card.appendChild(detail);
      var send = el('button', 'ip-prompt-send', 'Send');
      send.type = 'button';
      send.addEventListener('click', function () { finish(null, false); });
      if (!prompt.options || !prompt.options.length) card.appendChild(send);
    }

    if (prompt.key === 'invite' && prompt.invite_url) {
      card.appendChild(el('p', 'ip-prompt-note',
        'You both get ' + (prompt.reward_days || 30) + ' days of Pro when they start planning.'));
    }

    document.body.appendChild(card);
    requestAnimationFrame(function () { card.classList.add('is-in'); });
    IP.insight.track('prompt_shown', { step: prompt.key });
  }

  function copy(text, button) {
    var done = function () {
      var original = button.textContent;
      button.textContent = 'Link copied';
      setTimeout(function () { button.textContent = original; }, 1800);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, done);
    else done();
  }

  function ask() {
    if (quiet()) return;
    try {
      if (sessionStorage.getItem(SESSION_KEY)) return;
    } catch (e) { /* private mode: fall through, the server still rate-limits */ }
    fetch('/api/insight/prompt', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (body) {
        if (!body || !body.prompt) return;
        try { sessionStorage.setItem(SESSION_KEY, '1'); } catch (e) {}
        render(body.prompt);
      })
      .catch(function () {});
  }

  if (document.readyState === 'complete') setTimeout(ask, DELAY_MS);
  else window.addEventListener('load', function () { setTimeout(ask, DELAY_MS); });
})();
