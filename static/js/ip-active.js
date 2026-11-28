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
    finishing: false,
    saving: false
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
    paintCanvasTools();
    paintInstructions();

    $('ipaTitle').textContent = block.title || 'Study session';
    $('ipaCourse').textContent = block.course || 'Study';

    // Opens the shared block-detail panel. Active study shows one block
    // rather than a list, so it gets a button instead of a clickable card
    // -- the card here is full of controls, and making it clickable would
    // fire the panel every time someone reached for the timer.
    var resBtn = $('ipaResources');
    if (resBtn) {
      resBtn.hidden = false;
      resBtn.onclick = function () {
        if (!window.IPBlock) return;
        window.IPBlock.open({
          assignment: block.title || '',
          course: block.course || '',
          due_date: block.due_date || '',
          duration_minutes: block.planned_minutes || 0,
          description: assignmentInstructions(block),
          notes: (block.reasons && block.reasons.join(' ')) || ''
        });
      };
    }

    var due = $('ipaDue');
    if (block.due_date) {
      var date = new Date(block.due_date.slice(0, 10) + 'T12:00:00');
      due.textContent = Number.isNaN(date.getTime()) ? '' : 'Due ' + date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
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
      if (isFiveMinuteStart()) {
        offerToContinue();
      } else {
        status('Past your estimate. Keep going if you\'re in flow, or stop and log it. Either is useful.');
      }
    }
  }

  // ── "Just 5 minutes" ────────────────────────────────────────────────
  //
  // A session started from a "Break it down" step carries task_id
  // "step:<id>". The deal offered was five minutes, so when they are up the
  // page says so and asks — it does not silently keep counting, and it does
  // not stop the timer either: most people who get five minutes in want to
  // keep going, which is the whole reason the trick works.

  function isFiveMinuteStart() {
    var taskId = (IPA.session && IPA.session.task_id) || (IPA.plan && IPA.plan.task_id) || '';
    return /^step:\d+$/.test(String(taskId));
  }

  function offerToContinue() {
    var nudge = $('ipaNudge');
    var text = $('ipaNudgeText');
    if (!nudge || !text) {
      status('That\'s your 5 minutes. Keep going (the timer is still running) or stop and log it.');
      return;
    }
    text.textContent = 'That\'s your 5 minutes. Keep going? The timer is still running: ' +
      'or stop here and the time still counts.';
    var stop = $('ipaNudgeStop');
    if (!stop) {
      stop = document.createElement('button');
      stop.type = 'button';
      stop.id = 'ipaNudgeStop';
      stop.className = 'ipa-btn ipa-btn--ghost';
      stop.textContent = 'Stop here';
      stop.addEventListener('click', function () { nudge.hidden = true; openFinish(false); });
      nudge.appendChild(stop);
    }
    var keep = $('ipaNudgeDismiss');
    if (keep) keep.textContent = 'Keep going';
    nudge.hidden = false;
  }

  function setControls(mode) {
    var picker = $('ipaAssignmentSelect');
    if (picker) picker.disabled = mode === 'loading' || mode === 'running' || mode === 'paused';
    paintCanvasTools();
    // 'none' means there is nothing to act on at all. It exists because the
    // phone layout pins these controls to a fixed bar: an inert "Start"
    // sitting in the thumb zone with no session behind it is a button that
    // teaches the student it does nothing.
    var empty = mode === 'none' || mode === 'loading';
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

  var assignmentChoices = [];
  var assignmentGeneration = 0;
  function loadAssignments() {
    var select = $('ipaAssignmentSelect');
    if (!select) return;
    var signedIn = Boolean(document.body.dataset.userId);
    api(signedIn ? '/api/active/assignments' : '/tasks/unified').then(function (data) {
      var rows = Array.isArray(data) ? data : data?.overdue?.concat(data.today || [], data.upcoming || []);
      if (!Array.isArray(rows)) throw new Error('Invalid assignments');
      assignmentChoices = rows.filter(function (row) { return row.title && row.requires_action !== false; });
      assignmentChoices.forEach(function (row, index) {
        var option = document.createElement('option');
        option.value = String(index);
        option.textContent = row.title + (row.course ? ' · ' + row.course : '');
        select.insertBefore(option, select.lastElementChild);
      });
      $('ipaAssignmentStatus').textContent = assignmentChoices.length
        ? 'Pick the work you want to focus on.'
        : 'You can study with a session of your own.';
      paintCanvasTools();
      paintInstructions();
    }).catch(function () { $('ipaAssignmentStatus').textContent = 'Assignments unavailable. Your timer and current plan still work.'; });
    select.addEventListener('change', function () {
      if (IPA.session && !IPA.session.ended_at) return;
      assignmentGeneration++;
      if (select.value === '') { loadNext(null, requestedStudyDate); return; }
      hideStudyBreak();
      if (select.value === 'custom') {
        IPA.plan = null;
        $('ipaCard').hidden = true;
        $('ipaEmpty').hidden = false;
        $('ipaEmptyMsg').textContent = 'Start now, or adjust the session below.';
        showAdhoc(false);
        setControls('none');
        $('ipaAdhocOptions').open = true;
        $('ipaAdhocTitle').focus();
        return;
      }
      var row = assignmentChoices[Number(select.value)];
      if (!row) return;
      renderPlan({ title: row.title, course: row.course || '', task_id: row.task_id || row.id,
        kind: row.kind || 'homework', difficulty: row.difficulty || 'medium', due_date: row.due_date,
        description: row.description, study_medium: row.study_medium,
        assignment_url: row.source === 'canvas' ? row.assignment_url : null,
        planned_minutes: Math.max(1, Math.min(300, Number(row.estimated_time || row.est_minutes) || 25)) });
      setControls('idle');
    });
  }

  var requestedStudyDate = '';
  var breakTimer = null;

  function hideStudyBreak() {
    window.clearInterval(breakTimer);
    $('ipaBreak').hidden = true;
  }

  function showStudyBreak() {
    var end = Date.now() + 5 * 60 * 1000;
    $('ipaCard').hidden = true;
    $('ipaBreak').hidden = false;
    $('ipaBreakTime').textContent = '5:00';
    setControls('none');
    $('ipaSkipBreak').focus();
    breakTimer = window.setInterval(function () {
      var seconds = Math.ceil((end - Date.now()) / 1000);
      if (seconds <= 0) return finishStudyBreak();
      $('ipaBreakTime').textContent = fmt(seconds);
    }, 1000);
  }

  function finishStudyBreak() {
    hideStudyBreak();
    $('ipaCard').hidden = false;
    setControls('idle');
    $('ipaStart').focus();
  }

  function loadNext(blockId, blockDate, offerBreak) {
    hideStudyBreak();
    var generation = ++assignmentGeneration;
    if ($('ipaAssignmentSelect')) {
      $('ipaAssignmentSelect').value = '';
      $('ipaAssignmentSelect').disabled = true;
    }
    setControls('loading');
    return api('/api/active/current')
      .then(function (data) {
        if (generation !== assignmentGeneration) return;
        syncMediaPermission(data);
        if (data.session) return resume(data.session);
        var params = new URLSearchParams();
        if (blockId) params.set('block_id', blockId);
        if (blockDate) params.set('date', blockDate);
        var query = params.size ? '?' + params : '';
        return api('/api/active/next' + query).then(function (next) {
          if (generation !== assignmentGeneration) return;
          if (!next.next) {
            IPA.plan = null;
            $('ipaCard').hidden = true;
            $('ipaEmpty').hidden = false;
            /* Three different situations used to share one message telling
               the student to go build a plan — including the case where they
               had a plan and had finished it, which reads as the app having
               lost their work. Say which one it is, and offer a session
               either way rather than only a link away from here. */
            var hasPlan = !!next.has_plan;
            var finished = hasPlan && !!next.all_done;
            $('ipaEmptyMsg').textContent = next.requested_block_missing
              ? 'This block is no longer open. Select an assignment or start a session below.'
              : finished
              ? 'Everything in your plan is done. Start another session if you want to keep going.'
              : hasPlan
                ? 'Nothing left scheduled for now. You can still start a session below.'
                : 'No schedule yet. You do not need one. Start a session below, or build a plan.';
            showAdhoc(!hasPlan);
            setControls('none');
            return;
          }
          renderPlan(next.next);
          var starts = Date.parse(next.next.start_iso || '');
          if (offerBreak && next.next.date === offerBreak && starts - Date.now() >= 5 * 60 * 1000) {
            showStudyBreak();
          } else setControls('idle');
        });
      })
      .catch(function (err) {
        if (generation !== assignmentGeneration) return;
        IPA.plan = null;
        $('ipaCard').hidden = true;
        $('ipaEmpty').hidden = false;
        $('ipaEmptyMsg').textContent = 'Could not load your session. You can still start one below.';
        /* A failed lookup should not also cost the student the ability to
           study. The ad-hoc form does not depend on the plan. */
        showAdhoc(true);
        setControls('none');
      });
  }

  // ── Ad-hoc sessions ─────────────────────────────────────────────────
  //
  // Active was unusable without a generated plan. Studying for twenty
  // minutes is not a thing that should require a schedule first.

  function showAdhoc(offerScheduleLink) {
    var form = $('ipaAdhoc');
    if (form) form.hidden = false;
    var cta = $('ipaEmptyCta');
    if (cta) cta.hidden = !offerScheduleLink;
  }

  function adhocUpdateLabel() {
    $('ipaAdhocStart').textContent = 'Start ' + $('ipaAdhocDuration').value + '-minute session';
  }

  function adhocError(message) {
    var box = $('ipaAdhocError');
    if (!box) return;
    box.textContent = message || '';
    box.hidden = !message;
  }

  function adhocStart() {
    var start = $('ipaAdhocStart');
    if (start.disabled) return;
    if ($('ipaAssignmentSelect')) $('ipaAssignmentSelect').disabled = true;
    var title = $('ipaAdhocTitle').value.trim() || 'Focus session';
    var minutes = Number($('ipaAdhocDuration').value);
    start.disabled = true;
    start.textContent = 'Starting…';
    adhocError('');

    api('/api/active/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: title,
        planned_minutes: minutes,
        course: '',
        kind: 'study',
      }),
    }).then(function (data) {
      if (!data.session) throw new Error('No session came back.');
      var form = $('ipaAdhoc');
      if (form) form.hidden = true;
      resume(data.session);
    }).catch(function (err) {
      adhocError('Could not start that session: ' + err.message);
      if ($('ipaAssignmentSelect')) $('ipaAssignmentSelect').disabled = false;
      start.disabled = false;
      adhocUpdateLabel();
    });
  }

  // The markup calls these from inline handlers, so they need a home on the
  // window rather than staying private to the IIFE.
  window.IPActive = {
    adhocUpdateLabel: adhocUpdateLabel,
    adhocStart: adhocStart,
  };

  function resume(session) {
    var planContext = IPA.plan?.block_id && IPA.plan.block_id === session.block_id ? IPA.plan : null;
    IPA.session = session;
    IPA.accrued = session.active_seconds || 0;
    IPA.overNoted = false;
    IPA.difficulty = null;
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
      block_id: session.block_id,
      date: planContext?.date
    });
    if (session.state === 'paused') {
      IPA.running = false;
      setControls('paused');
      status('Paused. Picking up where you left off.');
    } else {
      beginTicking();
      if ($('ipaFocusToggle').checked) startTracker();
      setControls('running');
      status('Session resumed.');
    }
    paintClock();
  }

  function start() {
    if (!IPA.plan || $('ipaStart').disabled || IPA.session) return;
    if ($('ipaAssignmentSelect')) $('ipaAssignmentSelect').disabled = true;
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
      if (!data.session) throw new Error('Could not start the session.');
      var plan = IPA.plan;
      syncMediaPermission(data);
      resume(data.session);
      if (String(data.session.task_id || '') === String(plan.task_id || '') && data.session.title === plan.title) {
        IPA.plan.assignment_url = plan.assignment_url;
        IPA.plan.study_medium = plan.study_medium;
        IPA.plan.description = plan.description;
        paintCanvasTools();
        paintInstructions();
      }
      status('Session started.');
    }).catch(function (err) {
      if ($('ipaAssignmentSelect')) $('ipaAssignmentSelect').disabled = false;
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
    if (!IPA.session || IPA.saving) return;
    if (IPA.running) {
      IPA.accrued = elapsedSeconds();
      IPA.pausedAt = Date.now();
      IPA.pauseCount += 1;
      stopTicking();
      stopScreen();
      stopTracker();
      if ($('ipaFocusToggle').checked) $('ipaFocusNote').textContent = 'Camera paused with this study session.';
      setControls('paused');
      status('Paused.');
      sendHeartbeat('paused');
    } else {
      if (IPA.pausedAt) IPA.pausedTotal += Math.round((Date.now() - IPA.pausedAt) / 1000);
      IPA.pausedAt = 0;
      beginTicking();
      if ($('ipaFocusToggle').checked) startTracker();
      setControls('running');
      status('Back to it.');
      sendHeartbeat('running');
    }
  }

  function sendHeartbeat(forcedState, enableFocus) {
    if (!IPA.session) return Promise.resolve();
    var body = {
      active_seconds: elapsedSeconds(),
      paused_seconds: IPA.pausedTotal,
      pause_count: IPA.pauseCount,
      state: forcedState || (IPA.running ? 'running' : 'paused')
    };
    if (enableFocus === true) body.enable_focus = true;
    if (IPA.tracker) body.focus = { summary: IPA.tracker.snapshot() };
    return api('/api/active/' + IPA.session.id + '/heartbeat', {
      method: 'POST', body: JSON.stringify(body)
    }).then(function (data) {
      if (data.focus_allowed === false) denyCameraCheckIn(data.focus_reason);
      return data;
    }).catch(function () {
      // A dropped heartbeat is not worth interrupting anyone over; the next
      // one carries the same monotonic total, and finish() sends it again.
    });
  }

  // ── Finishing ───────────────────────────────────────────────────────

  function openFinish(completed) {
    if (IPA.saving) return;
    IPA.finishing = completed;
    $('ipaFinish').hidden = false;
    $('ipaProgress').value = completed ? 100 : Math.min(90, Math.round(
      (elapsedSeconds() / Math.max(1, (IPA.plan.planned_minutes || 1) * 60)) * 100));
    $('ipaProgressValue').textContent = $('ipaProgress').value + '%';
    $('ipaFinish').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function submitFinish() {
    if (!IPA.session || IPA.saving) return;
    IPA.saving = true;
    var wasRunning = IPA.running;
    $('ipaFinishSave').disabled = true;
    IPA.accrued = elapsedSeconds();
    stopTicking();
    stopScreen();
    paintCanvasTools();
    stopCameraCheckIn();
    var focusSummary = IPA.tracker ? IPA.tracker.snapshot() : null;

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
      IPA.tracker = null;
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
      var finishedDay = IPA.finishing && IPA.session.block_id && IPA.plan?.date;
      IPA.session = null;
      IPA.accrued = 0;
      IPA.overNoted = false;
      $('ipaAdhocTitle').value = '';
      $('ipaAdhocStart').disabled = false;
      adhocUpdateLabel();
      loadNext(null, requestedStudyDate, finishedDay);
    }).catch(function (err) {
      if (wasRunning) beginTicking();
      setControls(wasRunning ? 'running' : 'paused');
      status('Could not save. Try again to log this session.', true);
    }).finally(function () {
      IPA.saving = false;
      $('ipaFinishSave').disabled = false;
      paintCanvasTools();
    });
  }

  // The student chooses a tab. Live preview and optional clips stay in
  // this page, outside the session payload and AI pipeline.
  var sharedScreen = null;
  var screenRequest = 0;
  var screenStarted = 0;
  var screenTicker = null;
  var mediaAllowed = false;

  function linkedAssignment(block) {
    if (!block || block.task_id == null) return null;
    return assignmentChoices.find(function (choice) {
      var taskId = String(block.task_id);
      var choiceId = String(choice.task_id || choice.id);
      return (taskId === choiceId || taskId === choice.source + ':' + choiceId)
        && choice.title === block.title && (choice.course || '') === (block.course || '');
    });
  }

  function assignmentInstructions(block) {
    var row = linkedAssignment(block);
    return String((row && row.description) || (block && block.description) || '').trim().slice(0, 12000);
  }

  function paintInstructions() {
    var panel = $('ipaInstructions');
    if (!panel) return;
    var text = assignmentInstructions(IPA.plan);
    var line = $('ipaInstructionText');
    if (!text) {
      panel.hidden = true;
      return;
    }
    if (line.textContent !== text) {
      line.textContent = text;
      $('ipaResourceResult').hidden = true;
      $('ipaCheckResource').reset();
    }
    panel.hidden = false;
    var rule = text.split(/(?<=[.!?])\s+|\n/).find(function (part) {
      return part.length <= 300 && /\b(no (?:ai|chatgpt|outside sources)|closed.book|open.book|only (?:use|the assigned|the provided)|do not use|may use|is prohibited|are allowed)\b/i.test(part);
    });
    $('ipaInstructionRule').hidden = !rule;
    $('ipaInstructionRule').textContent = rule ? 'Check this instruction before using outside help: ' + rule : '';
    if (rule) panel.open = true;
  }

  function resourceAddress(value) {
    try {
      var url = new URL(value);
      return url.protocol === 'https:' && !url.username && !url.password ? url.origin + url.pathname : null;
    } catch (_) { return null; }
  }

  function checkResource(event) {
    event.preventDefault();
    var url = resourceAddress($('ipaResourceUrl').value.trim());
    var references = assignmentInstructions(IPA.plan).match(/https:\/\/[^\s<>"']+/g) || [];
    var result = $('ipaResourceResult');
    if (!url) result.textContent = 'Enter a secure https link.';
    else if (url === resourceAddress(canvasLink() || '')) result.textContent = 'This is the linked Canvas assignment.';
    else if (references.some(link => resourceAddress(link.replace(/[).,;]+$/, '')) === url)) {
      result.textContent = 'This link appears in the instructions. Check the surrounding text for how it may be used.';
    } else {
      result.textContent = 'This link is not listed in the instructions. Check with your teacher before using it.';
    }
    result.hidden = false;
  }

  function canvasLink() {
    if (!IPA.plan) return null;
    var row = linkedAssignment(IPA.plan);
    var candidate = IPA.plan.assignment_url || (row && row.source === 'canvas' && row.assignment_url);
    if (!candidate) return null;
    try {
      var url = new URL(candidate);
      if (url.protocol === 'https:' && !url.username && !url.password && !url.search && !url.hash
          && /^\/courses\/\d+\/assignments\/\d+\/?$/.test(url.pathname)) return url.href;
    } catch (_) {}
    return null;
  }

  function currentStudyMedium() {
    return IPA.plan?.study_medium || linkedAssignment(IPA.plan)?.study_medium || 'unspecified';
  }

  function paintCanvasTools() {
    var medium = currentStudyMedium();
    var panel = $('ipaFocusPanel');
    var timerOnly = medium === 'assessment';
    var hideCamera = timerOnly || medium === 'online';
    if (panel) panel.hidden = hideCamera;
    if (hideCamera && $('ipaFocusToggle').checked) {
      $('ipaFocusToggle').checked = false;
      stopTracker();
    }
    var url = canvasLink();
    var canShare = Boolean(mediaAllowed && url && navigator.mediaDevices?.getDisplayMedia);
    var note = $('ipaAssignmentMedia');
    if (note) note.textContent = !IPA.plan ? '' : timerOnly
      ? 'Quiz session. The timer works without camera or tab access.' : medium === 'online'
      ? (canShare ? 'Online assignment. You can share a browser tab after starting the timer.'
        : 'Online assignment. The study timer needs no camera or tab access.')
      : medium === 'paper' ? 'Paper assignment. Use the timer with an optional camera check-in.'
        : 'The study timer needs no camera or screen access.';
    var tools = $('ipaCanvasTools');
    if (!tools) return;
    var link = $('ipaCanvasLink');
    tools.hidden = !url;
    if (url) link.href = url;
    else link.removeAttribute('href');
    var button = $('ipaScreenShare');
    if (!button) return;
    button.hidden = !canShare || medium === 'paper' || timerOnly || !IPA.running || IPA.saving;
    button.textContent = sharedScreen ? 'Stop sharing' : 'Share Canvas tab';
    paintRecording();
  }

  function stopScreen(message) {
    screenRequest += 1;
    if (screenTicker) window.clearInterval(screenTicker);
    screenTicker = null;
    stopRecording();
    if (sharedScreen) sharedScreen.getTracks().forEach(function (track) { track.stop(); });
    sharedScreen = null;
    var video = $('ipaScreenVideo');
    if (!video) return;
    video.srcObject = null;
    $('ipaScreenPreview').hidden = true;
    $('ipaScreenPreview').open = false;
    var progress = $('ipaScreenStatus');
    progress.hidden = !message;
    progress.textContent = message || '';
    $('ipaScreenTime').hidden = true;
    $('ipaScreenShare').disabled = false;
    paintCanvasTools();
  }

  async function shareCanvas() {
    if (sharedScreen) { stopScreen('Tab sharing stopped. Your timer is still running.'); return; }
    if (!mediaAllowed || !IPA.running || !canvasLink() || ['paper', 'assessment'].includes(currentStudyMedium())) return;
    var request = ++screenRequest;
    var button = $('ipaScreenShare');
    button.disabled = true;
    try {
      var stream = await navigator.mediaDevices.getDisplayMedia({
        audio: false, video: { displaySurface: 'browser', frameRate: { ideal: 5, max: 10 } },
        selfBrowserSurface: 'exclude', monitorTypeSurfaces: 'exclude', surfaceSwitching: 'exclude'
      });
      var permission = await api('/api/active/focus-permission').catch(function () { return null; });
      if (!permission || permission.allowed !== true) {
        stream.getTracks().forEach(function (item) { item.stop(); });
        denyCameraCheckIn(permission && permission.reason);
        return;
      }
      var track = stream.getVideoTracks()[0];
      if (request !== screenRequest || !IPA.running || !mediaAllowed) {
        stream.getTracks().forEach(function (item) { item.stop(); });
        return;
      }
      if (!track || track.getSettings().displaySurface !== 'browser') {
        stream.getTracks().forEach(function (item) { item.stop(); });
        stopScreen('Choose a browser tab in the sharing picker.');
        return;
      }
      sharedScreen = stream;
      screenStarted = Date.now();
      $('ipaScreenVideo').srcObject = stream;
      $('ipaScreenPreview').hidden = false;
      $('ipaScreenPreview').open = true;
      var progress = $('ipaScreenStatus');
      progress.hidden = false;
      progress.textContent = 'Sharing your selected tab. Check that your Canvas assignment is in the preview.';
      var time = $('ipaScreenTime');
      time.hidden = false;
      function updateShareTime() {
        time.textContent = 'Shared for ' + fmt((Date.now() - screenStarted) / 1000);
      }
      updateShareTime();
      screenTicker = window.setInterval(updateShareTime, 1000);
      track.addEventListener('ended', function () {
        if (sharedScreen === stream) stopScreen('Tab sharing ended. Your timer is still running.');
      }, { once: true });
      paintCanvasTools();
    } catch (_) {
      if (request === screenRequest) stopScreen('Tab sharing did not start. Your study timer is still running.');
    } finally {
      if (request === screenRequest) button.disabled = false;
    }
  }

  var recorder = null;
  var recordedClip = null;
  var recordTimer = null;

  function paintRecording() {
    var group = $('ipaLocalRecording');
    if (!group) return;
    var active = !!(sharedScreen && window.MediaRecorder);
    group.hidden = !active && !recordedClip;
    $('ipaRecordTab').hidden = !active;
    $('ipaRecordTab').disabled = !!(recorder && recorder.state === 'inactive');
    $('ipaRecordTab').textContent = recorder ? 'Stop recording' : 'Record tab locally';
    $('ipaDownloadClip').hidden = !recordedClip;
    $('ipaDiscardClip').hidden = !recordedClip;
  }

  function stopRecording() {
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    window.clearTimeout(recordTimer);
  }

  function discardRecording() {
    recordedClip = null;
    $('ipaRecordStatus').textContent = 'Up to two minutes. Tab video only. Saved to your device only if you download it.';
    paintRecording();
  }

  function toggleRecording() {
    if (recorder) { stopRecording(); return; }
    if (!mediaAllowed || !sharedScreen || !window.MediaRecorder || !IPA.running) return;
    if (recordedClip) {
      $('ipaRecordStatus').textContent = 'Download or discard your last clip to start another one.';
      return;
    }
    try {
      var current = new MediaRecorder(sharedScreen, { videoBitsPerSecond: 600000 });
      var chunks = [];
      var size = 0;
      current.addEventListener('dataavailable', function (event) {
        if (!event.data.size) return;
        size += event.data.size;
        if (size <= 16 * 1024 * 1024) chunks.push(event.data);
        else stopRecording();
      });
      current.addEventListener('error', function () {
        chunks = [];
        $('ipaRecordStatus').textContent = 'Your browser could not record this tab.';
        stopRecording();
      });
      current.addEventListener('stop', function () {
        window.clearTimeout(recordTimer);
        recorder = null;
        recordedClip = mediaAllowed && chunks.length ? new Blob(chunks, { type: current.mimeType }) : null;
        $('ipaRecordStatus').textContent = recordedClip ? 'Clip ready to download or discard.' : 'No clip was saved.';
        paintRecording();
      });
      current.start(1000);
      recorder = current;
      recordTimer = window.setTimeout(stopRecording, 120000);
      $('ipaRecordStatus').textContent = 'Recording this tab on your device.';
    } catch (_) {
      $('ipaRecordStatus').textContent = 'Your browser could not record this tab.';
    }
    paintRecording();
  }

  function downloadRecording() {
    if (!mediaAllowed || !recordedClip) return;
    var url = URL.createObjectURL(recordedClip);
    var link = document.createElement('a');
    link.href = url;
    link.download = 'intelliplan-study.' + (recordedClip.type.includes('mp4') ? 'mp4' : 'webm');
    link.click();
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    discardRecording();
  }

  // ── Focus tracking ──────────────────────────────────────────────────

  function cameraOptions() {
    var select = $('ipaCameraSource');
    var value = select ? select.value : '';
    if (value === 'network') return { source: 'network', url: $('ipaNetworkUrl').value.trim(),
      username: $('ipaNetworkUser').value, password: $('ipaNetworkPassword').value };
    if (value === 'phone') return { source: 'phone', onInvite: function (url) {
      var wrap = $('ipaPhoneInvite');
      var input = $('ipaPhoneLink');
      if (input) input.value = url || '';
      if (wrap) wrap.hidden = !url;
      if (url && $('ipaCameraMenu')) $('ipaCameraMenu').open = true;
    } };
    return value.indexOf('device:') === 0 ? { deviceId: value.slice(7) }
      : { facingMode: value === 'rear' ? 'environment' : 'user' };
  }

  var cameraListGeneration = 0;
  function refreshCameras() {
    var select = $('ipaCameraSource');
    if (!select || !window.IPFocus) return;
    var generation = ++cameraListGeneration;
    return window.IPFocus.cameras().then(function (devices) {
      if (generation !== cameraListGeneration) return;
      var listStatus = $('ipaCameraListStatus');
      if (listStatus) listStatus.textContent = '';
      var selected = select.value;
      // Labels and identifiers stay in this page; they are never sent to us
      // or retained as a cross-session device fingerprint.
      for (var i = select.options.length - 1; i >= 0; i--) {
        if (select.options[i].value.indexOf('device:') === 0) select.remove(i);
      }
      devices.forEach(function (device) {
        if (!device.deviceId) return;
        var option = document.createElement('option');
        option.value = 'device:' + device.deviceId;
        option.textContent = device.label;
        select.add(option);
      });
      var found = Array.prototype.some.call(select.options, function (option) {
        return option.value === selected;
      });
      if (!found && selected.indexOf('device:') === 0) {
        var missing = document.createElement('option');
        missing.value = selected;
        missing.textContent = 'Selected camera is disconnected';
        select.add(missing);
      }
      select.value = selected;
    }).catch(function () {
      if (generation !== cameraListGeneration) return;
      var note = $('ipaCameraListStatus');
      if (note) note.textContent = 'Camera list unavailable. Allow camera access, then reopen this menu.';
    });
  }

  var cameraPolicyGeneration = 0;
  function syncMediaPermission(data) {
    if (data.focus_allowed === true) {
      mediaAllowed = true;
      paintCanvasTools();
    } else {
      denyCameraCheckIn(data.focus_reason);
    }
  }

  function denyCameraCheckIn(reason) {
    mediaAllowed = false;
    discardRecording();
    stopScreen();
    stopTracker();
    $('ipaFocusToggle').checked = false;
    $('ipaFocusToggle').disabled = true;
    var note = $('ipaFocusNote');
    note.textContent = reason === 'sign_in_required'
      ? 'Sign in to enable camera check-ins. Your guest study timer still works. '
      : 'Camera and tab sharing require an age-eligible account with workspace permission. Your timer still works.';
    if (reason === 'sign_in_required') {
      var signIn = document.createElement('a');
      signIn.href = '/login?next=%2Factive';
      signIn.textContent = 'Sign in';
      note.appendChild(signIn);
      note.appendChild(document.createTextNode('.'));
    }
    var method = $('ipaFocusMethod');
    if (method) method.hidden = true;
  }

  function startTracker() {
    if (['online', 'assessment'].includes(currentStudyMedium())) return;
    var generation = ++cameraPolicyGeneration;
    var focusState = $('ipaFocusState');
    if (focusState) focusState.hidden = false;
    $('ipaFocusStateText').textContent = 'Checking camera access';
    var stopButton = $('ipaFocusStop');
    if (stopButton) stopButton.hidden = false;
    return api('/api/active/focus-permission').then(function (permission) {
      if (generation !== cameraPolicyGeneration || !$('ipaFocusToggle').checked || !IPA.running) return;
      if (permission.allowed !== true) { denyCameraCheckIn(permission.reason); return; }
      startTrackerAllowed();
    }).catch(function () {
      if (generation !== cameraPolicyGeneration) return;
      stopTracker();
      $('ipaFocusToggle').checked = false;
      $('ipaFocusNote').textContent = 'Camera permissions could not be checked. Try again.';
    });
  }

  function startTrackerAllowed() {
    if (!window.IPFocus) return;
    if (IPA.tracker) {
      $('ipaFocusState').hidden = false;
      if (IPA.tracker.running) IPA.tracker.selectCamera(cameraOptions());
      else IPA.tracker.start(true, cameraOptions());
      startEnforcer();
      return;
    }
    var focus = IPA.session && IPA.session.focus || {};
    var startText = IPA.session && IPA.session.started_at || '';
    if (startText && !/(Z|[+-]\d\d:\d\d)$/.test(startText)) startText += 'Z';
    IPA.tracker = new window.IPFocus.Tracker({
      startedAt: Date.parse(startText) || Date.now() - elapsedSeconds() * 1000,
      summary: focus,
      onState: function (evt) {
        paintFocus(evt.state);
        // The tracker's state is the enforcer's only input. "present" is
        // the all-clear; anything else is the student not being here.
        // Driving it from state rather than from onDistraction matters:
        // onDistraction is rate-limited to one nudge every five minutes,
        // which is right for a suggestion and useless for enforcement.
        if (IPA.enforcer) {
          if (evt.state === 'present') IPA.enforcer.onReturn();
          else IPA.enforcer.onAway();
        }
      },
      onBucket: function (bucket) {
        if (!IPA.session) return;
        api('/api/active/' + IPA.session.id + '/heartbeat', {
          method: 'POST',
          body: JSON.stringify({ active_seconds: elapsedSeconds(), focus: bucket })
        }).then(function (data) {
          if (data.focus_allowed === false) denyCameraCheckIn(data.focus_reason);
        }).catch(function () {});
      },
      onDistraction: function () {
        $('ipaNudgeText').textContent =
          'Looks like you stepped away. Want to pause the timer so this doesn\'t count as study time?';
        $('ipaNudge').hidden = false;
      },
      onFraming: paintFraming,
      onCameraStatus: function (info) {
        $('ipaFocusNote').textContent = info.message;
        /* The toggle used to be forced back on whenever the camera failed,
           because cameraSupported() asked for an API that is almost never
           present — so a refusal left the switch on with nothing behind it.
           A failure now turns it off, which is what actually happened. */
        if (!info.ok && !info.pending) {
          $('ipaFocusToggle').checked = false;
          stopTracker();
        }

        /* Offer the self-view only once a camera is actually running.
           A "show me what the camera sees" button with no camera behind it
           is worse than none. */
        var selfWrap = $('ipaSelfViewWrap');
        if (selfWrap) selfWrap.hidden = !info.ok;
        if (!info.ok) hideSelfView();
        if (info.ok) {
          $('ipaFocusPanel').open = true;
          showSelfView();
          paintFraming('Center the area you want to show. Tilt the camera down a little if your face is at the bottom of the preview.');
          refreshCameras();
          sendHeartbeat(undefined, true);
        }

        var method = $('ipaFocusMethod');
        if (!method) return;
        if (info.ok && info.mode === 'motion') {
          method.textContent = 'Your browser has no on-device face detection, '
            + 'so this watches for movement instead. Movement can come from you '
            + 'or the background. It cannot tell that nobody is there, and '
            + 'does not prove you are studying. It will never nudge you for sitting still.';
          method.hidden = false;
        } else if (info.ok) {
          method.textContent = 'Using your study browser\'s on-device face detection. '
            + (info.source === 'phone' ? 'Video comes only from your paired phone; it is not recorded.'
              : 'Frames are examined and discarded, never stored or sent.');
          method.hidden = false;
        } else {
          method.hidden = true;
        }
      }
    });
    $('ipaFocusState').hidden = false;
    IPA.tracker.start(true, cameraOptions());
    startEnforcer();
  }

  /* Enforcement is configured per student and defaults to off, so this
     resolves to a no-op controller for anyone who never chose a mode. */
  function startEnforcer() {
    if (!window.IPEnforce || IPA.enforcer) return;
    var tracker = IPA.tracker;
    var generation = tracker && tracker.generation;
    window.IPEnforce.load().then(function (settings) {
      if (!IPA.running || IPA.tracker !== tracker || !tracker.running || tracker.generation !== generation
          || !$('ipaFocusToggle').checked || IPA.enforcer) return;
      if (!settings || settings.mode === 'off') return;
      IPA.enforcer = new window.IPEnforce.Enforcer({
        settings: settings,
        stakesHost: $('ipaStakesHost') || $('ipaNudge').parentNode,
        onForfeit: function (_amount, total) {
          // Report the running forfeit so the session record reflects it.
          // Fire-and-forget: this is bookkeeping, and a failed write must
          // not interrupt enforcement.
          if (!IPA.session) return;
          api('/api/active/' + IPA.session.id + '/forfeit', {
            method: 'POST',
            body: JSON.stringify({ sparks: total })
          }).catch(function () {});
        }
      });
      // Decode the alarm now, while they are still at the desk. Doing it
      // when the alarm is needed means a fetch and a decode at the one
      // moment latency is unacceptable.
      IPA.enforcer.prime();
    });
  }

  function stopTracker() {
    cameraPolicyGeneration += 1;
    // Always tear the enforcer down, even if the tracker is already gone —
    // an alarm that outlives the session it belongs to is the single worst
    // failure this feature can have.
    if (IPA.enforcer) { IPA.enforcer.stop(); IPA.enforcer = null; }
    hideSelfView();
    var selfWrap = $('ipaSelfViewWrap');
    if (selfWrap) selfWrap.hidden = true;
    var state = $('ipaFocusState');
    if (state) state.hidden = true;
    var stopButton = $('ipaFocusStop');
    if (stopButton) stopButton.hidden = true;
    $('ipaNudge').hidden = true;
    if (!IPA.tracker) return;
    IPA.tracker.stop();
  }

  function stopCameraCheckIn() {
    $('ipaFocusToggle').checked = false;
    stopTracker();
    $('ipaFocusNote').textContent = 'Camera check-in is off. The study timer still works.';
    var method = $('ipaFocusMethod');
    if (method) method.hidden = true;
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
    loadAssignments();
    // Start with nothing shown. loadNext() decides what the student can
    // actually do; showing a Start button before we know there is anything
    // to start makes the first paint a lie.
    setControls('none');

    $('ipaScreenShare')?.addEventListener('click', shareCanvas);
    $('ipaCheckResource')?.addEventListener('submit', checkResource);
    $('ipaRecordTab')?.addEventListener('click', toggleRecording);
    $('ipaDiscardClip')?.addEventListener('click', discardRecording);
    $('ipaDownloadClip')?.addEventListener('click', downloadRecording);
    $('ipaSkipBreak').addEventListener('click', finishStudyBreak);
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

    // "What leaves my device?" disclosure. Present in both layouts; absent
    // in neither, but guarded anyway so this controller never assumes markup
    // it does not own.
    var privacyBtn = $('ipaFocusPrivacy');
    var privacyText = $('ipaFocusPrivacyText');
    if (privacyBtn && privacyText) {
      privacyBtn.addEventListener('click', function () {
        var open = privacyBtn.getAttribute('aria-expanded') === 'true';
        privacyBtn.setAttribute('aria-expanded', String(!open));
        privacyText.hidden = open;
      });
    }

    var focusStop = $('ipaFocusStop');
    if (focusStop) focusStop.addEventListener('click', stopCameraCheckIn);

    $('ipaFocusToggle').addEventListener('change', function () {
      if (this.checked) {
        if (IPA.session && IPA.running) startTracker();
        else $('ipaFocusNote').textContent = 'Camera check-in will start when you start or resume your session.';
      } else {
        stopCameraCheckIn();
      }
    });

    var cameraSource = $('ipaCameraSource');
    if (cameraSource) {
      cameraSource.addEventListener('change', function () {
        var config = $('ipaNetworkConfig');
        if (config) config.hidden = cameraSource.value !== 'network';
        if (cameraSource.value === 'network') {
          stopTracker();
          $('ipaFocusToggle').checked = false;
          $('ipaFocusNote').textContent = 'Enter your camera gateway address, then turn on the check-in.';
          return;
        }
        if (!IPA.tracker || !$('ipaFocusToggle').checked) return;
        hideSelfView();
        startTracker();
      });
      ['ipaNetworkUrl', 'ipaNetworkUser', 'ipaNetworkPassword'].forEach(function (id) {
        var input = $(id);
        if (input) input.addEventListener('change', function () {
          if (cameraSource.value !== 'network') return;
          stopTracker(); $('ipaFocusToggle').checked = false;
          $('ipaFocusNote').textContent = 'Camera details changed. Turn on the check-in to reconnect.';
        });
      });
      var cameraMenu = $('ipaCameraMenu');
      if (cameraMenu) cameraMenu.addEventListener('toggle', function () {
        if (cameraMenu.open) refreshCameras();
      });
      if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
        navigator.mediaDevices.addEventListener('devicechange', function () {
          if (cameraMenu && cameraMenu.open || window.IPFocus && window.IPFocus.activeCamera()) refreshCameras();
        });
      }
    }

    // Last-gasp save. `keepalive` lets the request outlive the page, which
    // is the difference between a logged session and a phantom one when
    // someone closes the tab mid-study.
    window.addEventListener('pagehide', function () {
      stopScreen();
      stopTracker();
      if ($('ipaFocusToggle').checked) {
        $('ipaFocusToggle').checked = false;
        $('ipaFocusNote').textContent = 'Camera check-in stopped when you left. Turn it on to resume.';
      }
      if (!IPA.session || !IPA.running) return;
      try {
        navigator.sendBeacon(
          '/api/active/' + IPA.session.id + '/heartbeat',
          new Blob([JSON.stringify({ active_seconds: elapsedSeconds(), state: 'paused' })],
                   { type: 'application/json' })
        );
      } catch (e) { /* nothing useful to do here */ }
    });

    var params = new URLSearchParams(location.search);
    requestedStudyDate = params.get('date') || '';
    loadNext(params.get('block'), requestedStudyDate);
  });

  /* ── Self-view ────────────────────────────────────────────────────────
     Shows the student the frames being examined. Deliberately the same
     stream the detector reads rather than a second capture: the point is to
     make "nothing leaves your device" checkable instead of asserted, and a
     separate capture would prove nothing about the first. */

  function paintFraming(message) {
    var hint = $('ipaFraming');
    if (!hint) return;
    hint.hidden = !message;
    if (hint.textContent !== message) hint.textContent = message;
  }

  function hideSelfView() {
    var video = $('ipaSelfView');
    var note = $('ipaSelfViewNote');
    var btn = $('ipaSelfViewBtn');
    if (!video) return;
    paintFraming('');
    var camera = window.IPFocus && window.IPFocus.activeCamera();
    if (camera) camera.detachPreview(video);
    video.srcObject = null;
    video.hidden = true;
    if (note) note.hidden = true;
    if (btn) {
      btn.textContent = 'Show me what the camera sees';
      btn.setAttribute('aria-expanded', 'false');
    }
  }

  function showSelfView() {
    var video = $('ipaSelfView');
    var note = $('ipaSelfViewNote');
    var btn = $('ipaSelfViewBtn');
    if (!video || !btn) return;

    var camera = window.IPFocus && window.IPFocus.activeCamera();
    if (!camera || !camera.attachPreview(video)) {
      /* The camera stopped between the button appearing and being pressed.
         Say so rather than showing a black rectangle. */
      if (note) {
        note.textContent = 'The camera is not running right now.';
        note.hidden = false;
      }
      return;
    }

    video.hidden = false;
    if (note) {
      note.textContent = 'This is the picture being checked, on your device. '
        + 'It is not recorded. A paired phone sends video only to this study browser.';
      note.hidden = false;
    }
    btn.textContent = 'Hide the camera view';
    btn.setAttribute('aria-expanded', 'true');
  }

  window.toggleSelfView = function () {
    var video = $('ipaSelfView');
    if (video && !video.hidden) hideSelfView();
    else showSelfView();
  };

})();
