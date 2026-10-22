(() => {
  'use strict';
  const byId = (id) => document.getElementById(id);
  const state = { links: [], selected: null, latestNote: null, generation: 0, controller: null };
  const current = (studentId, generation) => state.selected === studentId && state.generation === generation;
  const status = (message) => { byId('familyStatus').textContent = message || ''; };
  byId('familyToday').textContent = new Date().toLocaleDateString(undefined,
    { weekday: 'long', month: 'long', day: 'numeric' });

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(response.status === 403
        ? 'Sharing is no longer available. Ask your student to review their sharing settings.'
        : 'Could not load Family. Please try again.');
      error.status = response.status;
      throw error;
    }
    return data;
  }

  async function loadLinks() {
    const data = await api('/api/roles/links');
    state.links = (data.links || []).filter((link) => link.relationship === 'parent');
    byId('familyLinkCount').textContent = `${state.links.filter((link) => link.accepted).length} connected`;
    const host = byId('familyStudents');
    host.replaceChildren();
    if (!state.links.length) {
      resetSelection();
      const empty = document.createElement('p');
      empty.className = 'family-no-students';
      empty.textContent = 'No linked students yet. Send a request above to get started.';
      host.append(empty);
      byId('familyDetail').hidden = true;
      status('Invite a student to see their approved progress here.');
      return;
    }
    state.links.forEach((link) => {
      const card = document.createElement('button');
      card.type = 'button';
      card.dataset.studentId = String(link.student_id);
      card.className = `family-student${link.accepted ? '' : ' pending'}`;
      card.disabled = !link.accepted;
      card.setAttribute('aria-pressed', String(state.selected === link.student_id));
      const title = document.createElement('strong');
      title.textContent = link.student_name;
      const detail = document.createElement('small');
      detail.textContent = link.accepted ? 'View their snapshot' : 'Waiting for student approval';
      card.append(title, detail);
      if (link.accepted) card.addEventListener('click', () => selectStudent(link));
      host.append(card);
    });
    const available = state.links.find((link) => link.accepted && link.student_id === state.selected)
      || state.links.find((link) => link.accepted);
    if (available) await selectStudent(available);
    else { resetSelection(); status('Waiting for your student to approve sharing.'); }
  }

  function resetSelection() {
    state.controller?.abort();
    state.generation++;
    state.selected = null;
    state.latestNote = null;
    byId('familyDetail').hidden = true;
    byId('familyRetry').hidden = true;
    byId('familyNudge').querySelector('button[type="submit"]').disabled = true;
    byId('familyWithdraw').hidden = true;
  }

  function setPrompt(summary) {
    const title = byId('familyPromptTitle');
    const body = byId('familyPrompt');
    if (['ok', 'partial'].includes(summary.work_status) && summary.overdue > 0) {
      title.textContent = 'What is making this one hard?';
      body.textContent = 'An item is past due. Ask what got in the way, then help choose one manageable next step.';
    } else if (['ok', 'partial'].includes(summary.work_status) && summary.upcoming?.length) {
      title.textContent = 'What is the first step?';
      body.textContent = `“${summary.upcoming[0].title}” is coming up. Ask what would make starting it easier.`;
    } else if (summary.study_status === 'ok' && summary.study_days_7d > 0) {
      title.textContent = 'What worked this week?';
      body.textContent = 'There is recorded practice. Ask which approach helped and what they want to try next.';
    } else {
      title.textContent = 'Start with curiosity.';
      body.textContent = summary.study_status === 'ok'
        ? 'No study session was recorded here this week. Ask what they are working on, without assuming they did not study.'
        : 'Ask what they are working on and what kind of support would help.';
    }
  }

  function renderUpcoming(items, workStatus) {
    const host = byId('familyUpcomingList');
    host.replaceChildren();
    if (!['ok', 'partial'].includes(workStatus) || !items?.length) {
      const row = document.createElement('li');
      row.textContent = workStatus === 'not_shared'
        ? 'The student chose not to share assignments.'
        : workStatus === 'unavailable'
          ? 'Assignment data is temporarily unavailable. Recorded study can still appear above.'
          : 'No upcoming assignments were found in this snapshot. Check the student app for the full plan.';
      host.append(row);
      return;
    }
    items.slice(0, 6).forEach((item) => {
      const row = document.createElement('li');
      const text = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = item.title || 'Untitled assignment';
      const course = document.createElement('small');
      course.textContent = item.course || 'Course not specified';
      const due = document.createElement('time');
      due.textContent = item.due_date
        ? new Date(`${item.due_date}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
        : 'No due date';
      if (item.due_date) due.dateTime = item.due_date;
      text.append(title, course);
      row.append(text, due);
      host.append(row);
    });
  }

  function renderFoundations(items, shared) {
    const host = byId('familyFoundationsList');
    host.replaceChildren();
    if (!shared || items === null || items === undefined) {
      const unavailable = document.createElement('p');
      unavailable.className = 'family-muted';
      unavailable.textContent = shared
        ? 'Foundations practice is temporarily unavailable. Please check again later.'
        : 'The student chose not to share Foundations practice.';
      host.append(unavailable);
      return;
    }
    if (!items?.length) {
      const empty = document.createElement('p');
      empty.className = 'family-muted';
      empty.textContent = 'No Foundations learner profile is connected to this account yet.';
      host.append(empty);
      return;
    }
    items.forEach((item) => {
      const card = document.createElement('article');
      const heading = document.createElement('h3');
      heading.textContent = `${item.learner_name} · ${item.grade === 13 ? 'College foundation' : item.grade === 0 ? 'Kindergarten' : `Grade ${item.grade}`}`;
      const evidence = document.createElement('p');
      evidence.textContent = `${item.answers_7d} ${item.answers_7d === 1 ? 'answer' : 'answers'} on ${item.answered_days_7d} ${item.answered_days_7d === 1 ? 'day' : 'days'} in the past week`;
      const next = document.createElement('p');
      next.textContent = item.focus?.skill
        ? `Next skill: ${item.focus.skill}. ${item.focus.try_together}`
        : 'A next skill will appear after practice begins.';
      card.append(heading, evidence, next);
      host.append(card);
    });
  }

  async function loadNote(studentId, generation) {
    const { latest, can_send: canSend, next_note_at: nextAt } = await api(`/api/roles/student/${studentId}/encouragement`);
    if (!current(studentId, generation)) return;
    state.latestNote = latest;
    const form = byId('familyNudge');
    const withdraw = byId('familyWithdraw');
    const active = latest && !latest.acknowledged_at && !latest.withdrawn_at;
    form.querySelector('button[type="submit"]').disabled = !canSend;
    withdraw.hidden = !active;
    withdraw.disabled = false;
    byId('familyNoteState').textContent = !latest ? 'No note waiting.'
      : latest.withdrawn_at ? 'The last note was withdrawn.'
      : latest.acknowledged_at ? 'The student marked your last note as seen.'
      : 'Your note is waiting in their Command Center.';
    if (nextAt && !active) byId('familyNoteState').textContent +=
      ` Another note is available after ${new Date(nextAt).toLocaleString()}.`;
  }

  async function selectStudent(link) {
    state.controller?.abort();
    state.controller = new AbortController();
    const generation = ++state.generation;
    state.selected = link.student_id;
    state.latestNote = null;
    byId('familyDetail').hidden = true;
    byId('familyRetry').hidden = true;
    byId('familyNudge').querySelector('button[type="submit"]').disabled = true;
    byId('familyWithdraw').hidden = true;
    byId('familyNoteState').textContent = 'Loading note status…';
    [...byId('familyStudents').querySelectorAll('button')].forEach((button) =>
      button.setAttribute('aria-pressed', String(Number(button.dataset.studentId) === link.student_id)));
    status('Loading the student snapshot…');
    try {
      const data = await api(`/api/roles/student/${link.student_id}/overview`, { signal: state.controller.signal });
      if (!current(link.student_id, generation)) return;
      const summary = data.summary || {};
      if (summary.status !== 'ok') throw new Error('Their work summary is temporarily unavailable.');
      byId('familySelectedName').textContent = data.student.name;
      byId('familyAsOf').textContent = `Updated ${new Date(summary.as_of).toLocaleString()}${summary.work_source ? ' · ' + summary.work_source : ''}`;
      byId('familyStudyDays').textContent = window.AdultProgress.value(summary, 'study_days_7d', 'study');
      byId('familySessions').textContent = window.AdultProgress.value(summary, 'study_sessions_7d', 'study');
      byId('familyOpen').textContent = window.AdultProgress.value(summary, 'open', 'work');
      byId('familyOverdue').textContent = window.AdultProgress.value(summary, 'overdue', 'work');
      window.AdultProgress.render(byId('familyProgress'), summary);
      byId('familyTrust').textContent = summary.study_status === 'not_shared'
        ? 'Recorded study is private at the student’s choice. Ask them directly how learning is going.'
        : summary.study_status === 'unavailable'
          ? 'Recorded study is temporarily unavailable. This does not say whether they studied.'
          : 'A session is reported by the student app. It cannot confirm study away from IntelliPlan or how much help was needed. A blank week means no session was recorded here.';
      setPrompt(summary);
      renderFoundations(summary.foundations, summary.scopes?.includes('foundations'));
      renderUpcoming(summary.upcoming, summary.work_status);
      const toolsHost = byId('familyToolsList');
      toolsHost.replaceChildren();
      if (!data.tools) {
        const item = document.createElement('li'); item.textContent = 'Tool access is private until the student chooses to share it.'; toolsHost.append(item);
      } else {
        for (const tool of data.tools) {
          const item = document.createElement('li');
          item.textContent = `${tool.name}: ${tool.allowed ? 'Available' : 'Restricted'}${!tool.allowed ? ' · ' + String(tool.reason || '').replaceAll('_', ' ') : ''}`;
          toolsHost.append(item);
        }
      }
      byId('familyDetail').hidden = false;
      status('Student-approved summary.');
      try { await loadNote(link.student_id, generation); }
      catch (_) {
        if (current(link.student_id, generation)) byId('familyNoteState').textContent = 'Notes are temporarily unavailable. Refresh progress to try again.';
      }
    } catch (error) {
      if (!current(link.student_id, generation) || error.name === 'AbortError') return;
      status(error.message);
      byId('familyRetry').hidden = false;
    }
  }

  const refresh = () => {
    const link = state.links.find(item => item.student_id === state.selected && item.accepted);
    if (link) selectStudent(link);
    else loadLinks().catch(error => { status(error.message); byId('familyRetry').hidden = false; });
  };
  byId('familyRefresh').addEventListener('click', refresh);
  byId('familyRetry').addEventListener('click', refresh);

  byId('familyEnable').addEventListener('click', async () => {
    byId('familyEnable').disabled = true;
    try {
      await api('/api/roles/role', { method: 'POST', body: JSON.stringify({ role: 'parent' }) });
      byId('familyOnboard').hidden = true;
      byId('familyWorkspace').hidden = false;
      await loadLinks();
    } catch (error) { status(error.message); byId('familyEnable').disabled = false; }
  });
  byId('familyInvite').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button');
    button.disabled = true;
    try {
      const email = byId('familyStudentEmail').value.trim();
      await api('/api/roles/invite', { method: 'POST', body: JSON.stringify({ student_email: email }) });
      byId('familyStudentEmail').value = '';
      await loadLinks();
      status('Request created. Your student can approve it from Settings → Linked accounts.');
    } catch (error) { status(error.message); }
    finally { button.disabled = false; }
  });
  byId('familyNudge').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!state.selected) return;
    const studentId = state.selected, generation = state.generation;
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await api(`/api/roles/student/${studentId}/encouragement`,
        { method: 'POST', body: JSON.stringify({ template_id: byId('familyNudgeType').value }) });
      await loadNote(studentId, generation);
      if (current(studentId, generation)) status('Your note is waiting in their Command Center.');
    } catch (error) {
      if (current(studentId, generation)) {
        status(error.message);
        if (error.status === 403) {
          byId('familyDetail').hidden = true;
          byId('familyRetry').hidden = false;
          state.latestNote = null;
        } else button.disabled = false;
      }
    }
  });
  byId('familyWithdraw').addEventListener('click', async () => {
    if (!state.selected || !state.latestNote) return;
    const studentId = state.selected, generation = state.generation, noteId = state.latestNote.id;
    byId('familyWithdraw').disabled = true;
    try {
      await api(`/api/roles/student/${studentId}/encouragement/${noteId}`,
        { method: 'DELETE' });
      await loadNote(studentId, generation);
      if (current(studentId, generation)) status('Note withdrawn.');
    } catch (error) { if (current(studentId, generation)) status(error.message); }
    finally { if (current(studentId, generation)) byId('familyWithdraw').disabled = false; }
  });

  if (document.body.dataset.parentMode === 'true') {
    byId('familyWorkspace').hidden = false;
    loadLinks().catch(error => { status(error.message); byId('familyRetry').hidden = false; });
  } else {
    byId('familyOnboard').hidden = false;
    status('Enable Family mode to invite a student.');
  }
})();
