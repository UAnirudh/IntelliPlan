/* ==========================================================================
   ip-report.js — user-facing bug reporting
   --------------------------------------------------------------------------
   Opens a dialog that pairs the user's description with the diagnostics we
   already have (route, viewport, theme, recent failures). The user can read
   exactly what gets attached before sending — nothing is collected silently.

   Entry points:
     IP.openBugReport({prefill, auto})   from an error state's "Report this"
     data-ip-bug-report attribute        from any button in a template

   Depends on ip-core.js.
   ========================================================================== */
(function (window, document) {
  'use strict';

  var IP = window.IP || (window.IP = {});
  if (!IP.request) { console.error('[ip-report] ip-core.js must load first'); return; }

  var MIN_LENGTH = 8;
  var MAX_LENGTH = 5000;
  var dialog = null;

  /**
   * Everything attached to a report. Deliberately excludes page content,
   * form values and anything a user typed — only environment and failures.
   */
  function collectDiagnostics(auto) {
    var d = {
      route:    location.pathname + location.search,
      viewport: window.innerWidth + '×' + window.innerHeight,
      screen:   (window.screen ? screen.width + '×' + screen.height : 'unknown'),
      theme:    document.documentElement.getAttribute('data-theme') || 'light',
      palette:  document.documentElement.getAttribute('data-palette') || 'default',
      online:   navigator.onLine !== false,
      language: navigator.language || '',
      timezone: (Intl.DateTimeFormat().resolvedOptions().timeZone || ''),
      ua:       navigator.userAgent,
      queued:   IP.queue ? IP.queue.size() : 0,
      recent:   IP.recentErrors ? IP.recentErrors() : []
    };
    if (auto) {
      d.trigger = {
        message: String(auto.message || ''),
        status:  auto.status || 0,
        code:    auto.code || '',
        url:     auto.url || ''
      };
    }
    return d;
  }

  function formatDiagnostics(d) {
    var lines = [
      'route      ' + d.route,
      'viewport   ' + d.viewport + '  (screen ' + d.screen + ')',
      'theme      ' + d.theme + ' / ' + d.palette,
      'network    ' + (d.online ? 'online' : 'offline') +
                      (d.queued ? ', ' + d.queued + ' change(s) queued' : ''),
      'locale     ' + d.language + '  ' + d.timezone,
      'browser    ' + d.ua
    ];
    if (d.trigger) {
      lines.push('', 'triggering error');
      lines.push('  ' + d.trigger.message);
      if (d.trigger.status) lines.push('  status ' + d.trigger.status + ' ' + d.trigger.url);
    }
    if (d.recent && d.recent.length) {
      lines.push('', 'recent failures (' + d.recent.length + ')');
      d.recent.slice(-5).forEach(function (r) {
        lines.push('  [' + r.kind + '] ' + r.message);
      });
    }
    return lines.join('\n');
  }

  function buildDialog() {
    var dlg = document.createElement('dialog');
    dlg.className = 'ip-bug-dialog';
    dlg.setAttribute('aria-labelledby', 'ipBugTitle');
    dlg.innerHTML =
      '<form class="ip-bug-dialog__form" method="dialog">' +
        '<h2 id="ipBugTitle">Report a problem</h2>' +
        '<p class="ip-bug-lede">Tell us what you were doing and what happened. ' +
          'We attach the technical details below so we can reproduce it.</p>' +

        '<div class="ip-bug-field">' +
          '<label for="ipBugKind">What kind of problem</label>' +
          '<select id="ipBugKind">' +
            '<option value="bug">Something is broken</option>' +
            '<option value="general">Something looks wrong</option>' +
            '<option value="feature">Something is missing</option>' +
          '</select>' +
        '</div>' +

        '<div class="ip-bug-field">' +
          '<label for="ipBugMessage">What happened</label>' +
          '<textarea id="ipBugMessage" maxlength="' + MAX_LENGTH + '" ' +
            'placeholder="I clicked Generate schedule and the page stayed empty."></textarea>' +
          '<p class="ip-bug-error" id="ipBugError" hidden></p>' +
        '</div>' +

        '<details class="ip-bug-diag">' +
          '<summary>Technical details attached to this report</summary>' +
          '<pre id="ipBugDiag"></pre>' +
        '</details>' +

        '<div class="ip-bug-actions">' +
          '<button type="button" class="ip-btn ip-btn--ghost" id="ipBugCancel">Cancel</button>' +
          '<button type="button" class="ip-btn ip-btn--primary" id="ipBugSend">Send report</button>' +
        '</div>' +
      '</form>';

    document.body.appendChild(dlg);

    dlg.querySelector('#ipBugCancel').addEventListener('click', function () { dlg.close(); });
    dlg.querySelector('#ipBugSend').addEventListener('click', function () { submit(dlg); });

    // Escape closes; the dialog element handles it, but clear errors on close.
    dlg.addEventListener('close', function () {
      dlg.querySelector('#ipBugError').hidden = true;
      dlg.querySelector('#ipBugMessage').removeAttribute('aria-invalid');
    });
    return dlg;
  }

  function submit(dlg) {
    var field  = dlg.querySelector('#ipBugMessage');
    var errEl  = dlg.querySelector('#ipBugError');
    var sendBtn = dlg.querySelector('#ipBugSend');
    var message = field.value.trim();

    if (message.length < MIN_LENGTH) {
      errEl.textContent = 'Add a little more detail so we can find the problem.';
      errEl.hidden = false;
      field.setAttribute('aria-invalid', 'true');
      field.focus();
      return;
    }
    errEl.hidden = true;
    field.removeAttribute('aria-invalid');

    var diag = dlg._diagnostics || collectDiagnostics();
    sendBtn.disabled = true;
    sendBtn.textContent = 'Sending…';

    IP.post('/api/bug-report', {
      category:    dlg.querySelector('#ipBugKind').value,
      message:     message,
      page_url:    location.href,
      diagnostics: diag
    }, { retryUnsafe: true }).then(function () {
      dlg.close();
      field.value = '';
      IP.toast('Report sent. Thanks for flagging it.', 'success');
    }).catch(function (err) {
      if (navigator.onLine === false || (err && err.retriable)) {
        IP.queue.push({
          url: '/api/bug-report',
          body: { category: dlg.querySelector('#ipBugKind').value, message: message,
                  page_url: location.href, diagnostics: diag },
          label: 'bug report'
        });
        dlg.close();
        field.value = '';
        IP.toast('Saved. Your report sends as soon as you are back online.', 'info');
        return;
      }
      errEl.textContent = err.friendly ? err.friendly() : 'Could not send the report.';
      errEl.hidden = false;
    }).then(function () {
      sendBtn.disabled = false;
      sendBtn.textContent = 'Send report';
    });
  }

  /**
   * @param {{prefill?:string, auto?:Error}} opts
   *   prefill — seeds the textarea (used when an error state reports itself)
   *   auto    — the ApiError that triggered this, attached to diagnostics
   */
  IP.openBugReport = function (opts) {
    opts = opts || {};
    if (!dialog) dialog = buildDialog();

    var diag = collectDiagnostics(opts.auto);
    dialog._diagnostics = diag;
    dialog.querySelector('#ipBugDiag').textContent = formatDiagnostics(diag);

    var field = dialog.querySelector('#ipBugMessage');
    if (opts.prefill && !field.value) field.value = opts.prefill;
    if (opts.auto) dialog.querySelector('#ipBugKind').value = 'bug';

    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    field.focus();
  };

  /* Any element with data-ip-bug-report opens the dialog. */
  document.addEventListener('click', function (e) {
    var trigger = e.target.closest && e.target.closest('[data-ip-bug-report]');
    if (!trigger) return;
    e.preventDefault();
    IP.openBugReport({ prefill: trigger.getAttribute('data-ip-bug-prefill') || '' });
  });

})(window, document);
