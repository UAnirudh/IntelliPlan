/* Active study controller.
 *
 * Everything lives inside one IIFE and every DOM id is prefixed `ipa` —
 * base.html shares a global scope with every page, so an unprefixed `state`
 * or `timer` here would collide with another template's script and take the
 * whole block down silently.
 *
 * Timer model: we never accumulate by incrementing a counter on an interval.
 * Intervals drift, throttle in background tabs, and stop entirely when a
 * laptop sleeps — all of which would under-count real study time and teach
 * the estimation model that sessions are shorter than they are. Instead we
 * record wall-clock marks and derive elapsed time from Date.now(), so the
 * number survives a sleeping machine and a throttled tab alike.
 */
(function () {
  'use strict';

  var HEARTBEAT_MS = 15000;

  var IPA = {
    session: null,       // server row
    plan: null,          // the block we're executing
    running: false,
    startedAt: 0,        // wall clock of the current run segment
    accrued: 0,          // seconds banked from previous segments
    pausedTotal: 0,
    pauseCount: 0,
    pausedAt: 0,
    heartbeat: null,
    ticker: null,
    tracker: null,
    difficulty: null,
    finishing: false
  };

  function $(id) { return document.getElementById(id); }

  function elapsedSeconds() {
    if (!IPA.running) return IPA.accrued;
    return IPA.accrued + Math.max(0, Math.round((Date.now() - IPA.startedAt) / 1000));
  }

  function fmt(seconds) {
    var s = Math.max(0, Math.round(seconds));
    var m = Math.floor(s / 60);
    var r = s % 60;
    return (m < 10 ? '0' : '') + m + ':' + (r < 10 ? '0' : '') + r;
  }

  function api(path, options) {
    return fetch(path, Object.assign({
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin'
    }, options || {})).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          var err = new Error(body.error || ('Request failed (' + res.status + ')'));
          err.status = res.status;
          throw err;
        });
      }
      return res.json();
    });
  }

  function status(message, isError) {
    var el = $('ipaStatus');
    el.textContent = message || '';
    el.style.color = isError ? 'var(--danger-text)' : '';
  }

  // ── Rendering ───────────────────────────────────────────────────────

  function renderPlan(block) {
    IPA.plan = block;
    $('ipaEmpty').hidden = true;
    $('ipaCard').hidden = false;

    $('ipaTitle').textContent = block.title || 'Study session';
    $('ipaCourse').textContent = block.course || 'Study';

    var due = $('ipaDue');
    if (block.due_date) {
      var days = Math.round((new Date(block.due_date) - new Date()) / 86400000);
      due.textContent = days <= 0 ? 'due today' : (days === 1 ? 'due tomorrow' : 'due in ' + days + ' days');
    } else {
      due.textContent = block.planned_minutes + ' min';
    }

    var part = $('ipaPart');
    if (block.label && block.label !== block.title) {
      part.textContent = block.label;
      part.hidden = false;
    } else {
      part.hidden = true;
    }

    var reasons = $('ipaReasons');
    reasons.textContent = '';
    if (block.reasons && block.reasons.length) {
      block.reasons.forEach(function (reason) {
        var li = document.createElement('li');
        li.textContent = reason;
        reasons.appendChild(li);
      });
      reasons.hidden = false;
    } else {
      reasons.hidden = true;
    }

    $('ipaClockLabel').textContent = 'of ' + block.planned_minutes + ' min';
    paintClock();
  }

  function paintClock() {
    var planned = (IPA.plan && IPA.plan.planned_minutes ? IPA.plan.planned_minutes : 0) * 60;
    var elapsed = elapsedSeconds();
    $('ipaClock').textContent = fmt(elapsed);

    var circumference = 339.29;
    var ratio = planned > 0 ? Math.min(1, elapsed / planned) : 0;
    $('ipaRingFill').setAttribute('stroke-dashoffset', String(circumference * (1 - ratio)));
    $('ipaRing').setAttribute('data-over', planned > 0 && elapsed > planned ? '1' : '0');
    $('ipaRing').setAttribute('aria-label',
      'Session timer, ' + Math.round(elapsed / 60) + ' of ' + Math.round(planned / 60) + ' minutes');

    if (planned > 0 && elapsed > planned && !IPA.overNoted) {
      IPA.overNoted = true;
      status('Past your estimate — keep going if you\'re in flow, or stop and log it. Either is useful.');
    }
  }

  function setControls(mode) {
    // 'none' means there is nothing to act on at all. It exists because the
    // phone layout pins these controls to a fixed bar: an inert "Start"
    // sitting in the thumb zone with no session behind it is a button that
    // teaches the student it does nothing.
    var empty = mode === 'none';
    $('ipaStart').hidden = empty || mode !== 'idle';
    $('ipaPause').hidden = empty || mode === 'idle';
    $('ipaDone').hidden = empty || mode === 'idle';
    $('ipaQuit').hidden = empty || mode === 'idle';
    $('ipaPause').textContent = mode === 'paused' ? 'Resume' : 'Pause';

    // Hide the bar itself when it holds nothing, so the layout does not
    // reserve thumb-zone space for an empty strip.
    var bar = $('ipaControlBar');
    if (bar) bar.hidden = empty;
  }

  // ── Session lifecycle ───────────────────────────────────────────────

  function loadNext() {
    return api('/api/active/current')
      .then(function (data) {
        if (data.session) return resume(data.session);
        return api('/api/active/next').then(function (next) {
          if (!next.next) {
            $('ipaEmptyMsg').textContent =
              'Nothing scheduled right now. Build a plan and your next session shows up here.';
            $('ipaEmptyCta').hidden = false;
            setControls('none');
            return;
          }
          renderPlan(next.next);
          setControls('idle');
        });
      })
      .catch(function (err) {
        $('ipaEmptyMsg').textContent = 'Could not load your session: ' + err.message;
        setControls('none');
      });
  }

  function resume(session) {
    IPA.session = session;
    IPA.accrued = session.active_seconds || 0;
    IPA.pausedTotal = session.paused_seconds || 0;
    IPA.pauseCount = session.pause_count || 0;
    renderPlan({
      title: session.title,
      label: session.title,
      course: session.course,
      planned_minutes: session.planned_minutes,
      due_date: session.due_date,
      reasons: [],
      task_id: session.task_id,
      block_id: session.block_id
    });
    if (session.state === 'paused') {
      IPA.running = false;
      setControls('paused');
      status('Paused. Picking up where you left off.');
    } else {
      beginTicking();
      setControls('running');
      status('Session resumed.');
    }
    paintClock();
  }

  function start() {
    if (!IPA.plan) return;
    $('ipaStart').disabled = true;
    api('/api/active/start', {
      method: 'POST',
      body: JSON.stringify({
        title: IPA.plan.title,
        planned_minutes: IPA.plan.planned_minutes,
        block_id: IPA.plan.block_id,
        task_id: IPA.plan.task_id,
        course: IPA.plan.course,
        kind: IPA.plan.kind,
        difficulty: (IPA.plan.difficulty || 'medium').toLowerCase(),
        due_date: IPA.plan.due_date,
        focus_enabled: $('ipaFocusToggle').checked
      })
    }).then(function (data) {
      IPA.session = data.session;
      IPA.accrued = 0;
      beginTicking();
      setControls('running');
      status('Session started. The timer keeps running if the tab sleeps.');
      if ($('ipaFocusToggle').checked) startTracker();
    }).catch(function (err) {
      status(err.message, true);
    }).then(function () {
      $('ipaStart').disabled = false;
    });
  }

  function beginTicking() {
    IPA.running = true;
    IPA.startedAt = Date.now();
    if (IPA.ticker) window.clearInterval(IPA.ticker);
    IPA.ticker = window.setInterval(paintClock, 1000);
    if (IPA.heartbeat) window.clearInterval(IPA.heartbeat);
    IPA.heartbeat = window.setInterval(sendHeartbeat, HEARTBEAT_MS);
  }

  function stopTicking() {
    IPA.running = false;
    if (IPA.ticker) { window.clearInterval(IPA.ticker); IPA.ticker = null; }
    if (IPA.heartbeat) { window.clearInterval(IPA.heartbeat); IPA.heartbeat = null; }
  }

  function togglePause() {
    if (!IPA.session) return;
    if (IPA.running) {
      IPA.accrued = elapsedSeconds();
      IPA.pausedAt = Date.now();
      IPA.pauseCount += 1;
      stopTicking();
      setControls('paused');
      status('Paused.');
      sendHeartbeat('paused');
    } else {
      if (IPA.pausedAt) IPA.pausedTotal += Math.round((Date.now() - IPA.pausedAt) / 1000);
      IPA.pausedAt = 0;
      beginTicking();
      setControls('running');
      status('Back to it.');
      sendHeartbeat('running');
    }
  }

  function sendHeartbeat(forcedState) {
    if (!IPA.session) return Promise.resolve();
    var body = {
      active_seconds: elapsedSeconds(),
      paused_seconds: IPA.pausedTotal,
      pause_count: IPA.pauseCount,
      state: forcedState || (IPA.running ? 'running' : 'paused')
    };
    if (IPA.tracker) body.focus = { summary: IPA.tracker.snapshot() };
    return api('/api/active/' + IPA.session.id + '/heartbeat', {
      method: 'POST', body: JSON.stringify(body)
    }).catch(function () {
      // A dropped heartbeat is not worth interrupting anyone over; the next
      // one carries the same monotonic total, and finish() sends it again.
    });
  }

  // ── Finishing ───────────────────────────────────────────────────────

  function openFinish(completed) {
    IPA.finishing = completed;
    $('ipaFinish').hidden = false;
    $('ipaProgress').value = completed ? 100 : Math.min(90, Math.round(
      (elapsedSeconds() / Math.max(1, (IPA.plan.planned_minutes || 1) * 60)) * 100));
    $('ipaProgressValue').textContent = $('ipaProgress').value + '%';
    $('ipaFinish').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function submitFinish() {
    if (!IPA.session) return;
    $('ipaFinishSave').disabled = true;
    stopTicking();
    var focusSummary = IPA.tracker ? IPA.tracker.stop() : null;
    IPA.tracker = null;

    api('/api/active/' + IPA.session.id + '/finish', {
      method: 'POST',
      body: JSON.stringify({
        completed: !!IPA.finishing,
        active_seconds: elapsedSeconds(),
        progress_percent: parseInt($('ipaProgress').value, 10),
        perceived_difficulty: IPA.difficulty,
        focus: focusSummary ? { summary: focusSummary } : undefined
      })
    }).then(function (data) {
      $('ipaFinish').hidden = true;
      // Not 'idle' — idle means "a session is queued and ready to start",
      // and between finishing and loadNext() returning there is nothing.
      setControls('none');
      $('ipaCard').hidden = true;
      var learned = $('ipaLearned');
      if (data.learned && data.learned.message) {
        learned.textContent = data.learned.message;
        learned.hidden = false;
      }
      $('ipaEmpty').hidden = false;
      $('ipaEmptyMsg').textContent = 'Session logged. Loading what\'s next…';
      IPA.session = null;
      IPA.accrued = 0;
      IPA.overNoted = false;
      window.setTimeout(loadNext, 900);
    }).catch(function (err) {
      status(err.message, true);
    }).then(function () {
      $('ipaFinishSave').disabled = false;
    });
  }

  // ── Focus tracking ──────────────────────────────────────────────────

  function startTracker() {
    if (!window.IPFocus || IPA.tracker) return;
    IPA.tracker = new window.IPFocus.Tracker({
      onState: function (evt) { paintFocus(evt.state); },
      onBucket: function (bucket) {
        if (!IPA.session) return;
        api('/api/active/' + IPA.session.id + '/heartbeat', {
          method: 'POST',
          body: JSON.stringify({ active_seconds: elapsedSeconds(), focus: bucket })
        }).catch(function () {});
      },
      onDistraction: function () {
        $('ipaNudgeText').textContent =
          'Looks like you stepped away. Want to pause the timer so this doesn\'t count as study time?';
        $('ipaNudge').hidden = false;
      },
      onCameraStatus: function (info) {
        $('ipaFocusNote').textContent = info.message;
        if (!info.ok) $('ipaFocusToggle').checked = window.IPFocus.cameraSupported();
      }
    });
    $('ipaFocusState').hidden = false;
    IPA.tracker.start(true);
  }

  function stopTracker() {
    if (!IPA.tracker) return;
    IPA.tracker.stop();
    IPA.tracker = null;
    $('ipaFocusState').hidden = true;
    $('ipaNudge').hidden = true;
  }

  function paintFocus(state) {
    var el = $('ipaFocusState');
    el.setAttribute('data-state', state);
    $('ipaFocusStateText').textContent = ({
      present: 'At your desk',
      away: 'Looking away',
      absent: 'Not detected',
      unknown: 'Checking…'
    })[state] || 'Checking…';
    if (state === 'present') $('ipaNudge').hidden = true;
  }

  // ── Wiring ──────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    // Start with nothing shown. loadNext() decides what the student can
    // actually do; showing a Start button before we know there is anything
    // to start makes the first paint a lie.
    setControls('none');

    $('ipaStart').addEventListener('click', start);
    $('ipaPause').addEventListener('click', togglePause);
    $('ipaDone').addEventListener('click', function () { openFinish(true); });
    $('ipaQuit').addEventListener('click', function () { openFinish(false); });
    $('ipaFinishSave').addEventListener('click', submitFinish);
    $('ipaFinishCancel').addEventListener('click', function () { $('ipaFinish').hidden = true; });
    $('ipaNudgeDismiss').addEventListener('click', function () { $('ipaNudge').hidden = true; });

    $('ipaProgress').addEventListener('input', function () {
      $('ipaProgressValue').textContent = this.value + '%';
    });

    Array.prototype.forEach.call($('ipaDifficulty').querySelectorAll('button'), function (btn) {
      btn.addEventListener('click', function () {
        Array.prototype.forEach.call($('ipaDifficulty').querySelectorAll('button'), function (b) {
          b.setAttribute('aria-pressed', String(b === btn));
        });
        IPA.difficulty = parseInt(btn.getAttribute('data-value'), 10);
      });
    });

    $('ipaFocusToggle').addEventListener('change', function () {
      if (this.checked) {
        if (IPA.session) startTracker();
      } else {
        stopTracker();
      }
    });

    // Last-gasp save. `keepalive` lets the request outlive the page, which
    // is the difference between a logged session and a phantom one when
    // someone closes the tab mid-study.
    window.addEventListener('pagehide', function () {
      if (!IPA.session || !IPA.running) return;
      try {
        navigator.sendBeacon(
          '/api/active/' + IPA.session.id + '/heartbeat',
          new Blob([JSON.stringify({ active_seconds: elapsedSeconds(), state: 'paused' })],
                   { type: 'application/json' })
        );
      } catch (e) { /* nothing useful to do here */ }
    });

    loadNext();
  });
})();
