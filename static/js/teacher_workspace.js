(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const state = { links: [], summaries: new Map(), versions: new Map(), generation: 0, rosterGeneration: 0, pendingStudentId: null, previousFocus: null, controller: null, overflow: '' };
  // The dialog must remain interactive while the background workspace is inert.
  document.body.append(byId('tdDetail'));
  const feedback = (message, kind = 'error') => {
    const node = byId('tdFeedback');
    node.textContent = message;
    node.className = 'td-feedback visible ' + kind;
  };
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options });
    if (!response.ok) {
      const error = new Error(response.status === 403 ? 'Sharing is no longer available. Ask the student to review their sharing settings.' : 'Could not load shared progress. Try again.');
      error.status = response.status;
      throw error;
    }
    return response.json();
  }
  function stats(host, summary) {
    host.replaceChildren();
    [['open', 'Open tasks', 'work'], ['overdue', 'Past due', 'work'], ['study_days_7d', 'Practice days · 7 days', 'study']].forEach(([key, label, source]) => {
      const stat = make('div', null, 'td-stat');
      stat.append(make('div', window.AdultProgress.value(summary, key, source), 'td-stat-val'), make('div', label, 'td-stat-label'));
      host.append(stat);
    });
  }
  function filter() {
    const query = byId('tdSearch').value.trim().toLowerCase();
    const mode = byId('tdFilter').value;
    let visible = 0;
    byId('tdStudents').querySelectorAll('.td-student-card').forEach(card => {
      const summary = state.summaries.get(Number(card.dataset.studentId));
      const attention = ['ok', 'partial'].includes(summary?.work_status) && summary.overdue > 0;
      const matches = card.dataset.search.includes(query) &&
        (mode === 'all' || (mode === 'pending' ? card.dataset.accepted === 'false' : attention));
      card.hidden = !matches;
      if (matches) visible++;
    });
    const waiting = state.links.some(link => link.accepted && !state.summaries.has(link.student_id));
    byId('tdRosterStatus').textContent = !state.links.length ? '' :
      visible + ' of ' + state.links.length + ' students shown' + (waiting ? ' · loading shared progress…' : '');
    byId('tdNoResults').hidden = !state.links.length || visible > 0 || waiting;
  }
  function unavailable(studentId) {
    state.summaries.set(studentId, {});
    const host = byId('tdStats-' + studentId);
    if (host) host.replaceChildren(make('p', 'Progress unavailable. Open this student to retry.', 'td-consent-note'));
    filter();
  }
  async function loadStats(link, rosterGeneration) {
    const host = byId('tdStats-' + link.student_id);
    const version = state.versions.get(link.student_id);
    try {
      const data = await api('/api/roles/student/' + link.student_id + '/overview');
      if (rosterGeneration !== state.rosterGeneration || version !== state.versions.get(link.student_id)) return;
      state.summaries.set(link.student_id, data.summary || {});
      if (host?.isConnected) stats(host, data.summary || {});
    } catch (_) {
      if (rosterGeneration !== state.rosterGeneration || version !== state.versions.get(link.student_id)) return;
      state.summaries.set(link.student_id, {});
      if (host?.isConnected) host.replaceChildren(make('p', 'Progress unavailable. Open this student to retry.', 'td-consent-note'));
    }
    filter();
  }
  async function load() {
    const rosterGeneration = ++state.rosterGeneration;
    byId('tdSkeleton').hidden = false;
    byId('tdRosterRetry').hidden = true;
    try {
      const data = await api('/api/roles/links');
      if (rosterGeneration !== state.rosterGeneration) return;
      state.links = (data.links || []).filter(link => link.relationship === 'teacher');
      state.summaries.clear();
      byId('tdFeedback').classList.remove('visible');
      byId('tdRosterCount').textContent = state.links.filter(link => link.accepted).length + ' connected · ' + state.links.filter(link => !link.accepted).length + ' awaiting approval';
      byId('tdStudents').replaceChildren();
      byId('tdEmpty').style.display = state.links.length ? 'none' : 'block';
      for (const link of state.links) {
        const card = make('div', null, 'td-student-card' + (link.accepted ? '' : ' pending'));
        card.dataset.studentId = String(link.student_id);
        card.dataset.accepted = String(link.accepted);
        card.dataset.search = (link.student_name + ' ' + link.student_email).toLowerCase();
        const identity = make('div');
        identity.append(make('div', link.student_name, 'td-student-name'), make('div', link.student_email, 'td-student-email'));
        card.append(identity);
        if (link.accepted) {
          const host = make('div', 'Loading shared progress…', 'td-stats');
          host.id = 'tdStats-' + link.student_id;
          card.append(host);
          card.tabIndex = 0;
          card.setAttribute('role', 'button');
          card.setAttribute('aria-label', 'View ' + link.student_name + "'s overview");
          card.addEventListener('click', () => open(link));
          card.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(link); }
          });
        } else card.append(make('span', 'Awaiting consent', 'td-pill'), make('p', 'The student needs to approve your invitation before progress is shared.', 'td-consent-note'));
        byId('tdStudents').append(card);
      }
      filter();
      byId('tdSkeleton').hidden = true;
      // Bound concurrency so a large roster cannot flood the server.
      const queue = state.links.filter(link => link.accepted);
      await Promise.all(Array.from({ length: Math.min(3, queue.length) }, async () => {
        while (queue.length && rosterGeneration === state.rosterGeneration) await loadStats(queue.shift(), rosterGeneration);
      }));
    } catch (error) {
      if (rosterGeneration === state.rosterGeneration) {
        feedback(error.message);
        byId('tdRosterRetry').hidden = false;
      }
    }
    finally { if (rosterGeneration === state.rosterGeneration) byId('tdSkeleton').hidden = true; }
  }
  function closeButton() {
    const button = make('button', 'Close', 'btn-secondary');
    button.type = 'button';
    button.addEventListener('click', close);
    return button;
  }
  async function open(link) {
    if (!byId('tdDetail').classList.contains('open')) {
      state.previousFocus = document.activeElement;
      state.overflow = document.body.style.overflow;
    }
    state.controller?.abort();
    state.controller = new AbortController();
    const generation = ++state.generation;
    state.pendingStudentId = link.student_id;
    state.versions.set(link.student_id, (state.versions.get(link.student_id) || 0) + 1);
    const box = byId('tdDetailBox');
    const title = make('h2', link.student_name, 'td-detail-title');
    title.id = 'tdDetailTitle';
    box.replaceChildren(title, make('p', 'Loading shared progress…'), closeButton());
    byId('tdDetail').classList.add('open');
    document.querySelector('.adult-layout').inert = true;
    document.body.style.overflow = 'hidden';
    box.focus();
    try {
      const data = await api('/api/roles/student/' + link.student_id + '/overview', { signal: state.controller.signal });
      if (generation !== state.generation) return;
      state.pendingStudentId = null;
      const summary = data.summary || {};
      state.summaries.set(link.student_id, summary);
      const rosterStats = byId('tdStats-' + link.student_id);
      if (rosterStats) stats(rosterStats, summary);
      filter();
      const restoreFocus = box.contains(document.activeElement);
      box.replaceChildren(title, make('p', 'Student-approved progress. Private conversations and answers stay private.', 'td-detail-note'));
      box.append(make('h3', 'Recorded progress', 'td-detail-heading'));
      const progress = make('div', null, 'adult-progress-panel');
      window.AdultProgress.render(progress, summary);
      box.append(progress, make('h3', 'Upcoming shared work', 'td-detail-heading'));
      const upcoming = make('ul', null, 'td-detail-list');
      if (!['ok', 'partial'].includes(summary.work_status)) upcoming.append(make('li', summary.work_status === 'not_shared' ? 'Assignment details have not been shared.' : 'Assignment details are temporarily unavailable.'));
      else if (!summary.upcoming?.length) upcoming.append(make('li', 'No upcoming shared assignments.'));
      else for (const task of summary.upcoming) upcoming.append(make('li', (task.title || 'Assignment') + ' · ' + (task.course || 'No course') + ' · ' + (task.due_date || 'No due date')));
      box.append(upcoming, make('h3', 'Practice and next skills', 'td-detail-heading'));
      const foundations = make('ul', null, 'td-detail-list');
      if (!summary.scopes?.includes('foundations')) foundations.append(make('li', 'Foundations practice has not been shared.'));
      else if (summary.foundations == null) foundations.append(make('li', 'Foundations practice is temporarily unavailable.'));
      else if (!summary.foundations.length) foundations.append(make('li', 'No Foundations practice recorded yet.'));
      else for (const learner of summary.foundations) foundations.append(make('li', learner.learner_name + ': ' + learner.answers_7d + ' practice answers in 7 days' + (learner.focus?.skill ? '. Next skill: ' + learner.focus.skill : '')));
      box.append(foundations);
      const tools = make('details', null, 'td-tools');
      const toolsTitle = make('summary');
      toolsTitle.append(make('h3', 'Tools permitted by their institution', 'td-detail-heading'));
      const toolList = make('ul', null, 'td-detail-list');
      if (!data.tools) toolList.append(make('li', 'Tool access has not been shared.'));
      else for (const tool of data.tools) toolList.append(make('li', tool.name + ' · ' + (tool.allowed ? 'Available' : 'Restricted: ' + String(tool.reason || '').replaceAll('_', ' '))));
      tools.append(toolsTitle, toolList);
      box.append(tools, closeButton());
      if (restoreFocus) box.focus();
    } catch (error) {
      if (generation !== state.generation || error.name === 'AbortError') return;
      state.pendingStudentId = null;
      unavailable(link.student_id);
      const restoreFocus = box.contains(document.activeElement);
      const retry = make('button', 'Retry progress', 'btn-primary');
      retry.type = 'button';
      retry.addEventListener('click', () => open(link));
      box.replaceChildren(title, make('p', error.message, 'td-detail-note'), retry, closeButton());
      if (restoreFocus) box.focus();
    }
  }
  function close() {
    state.generation++;
    state.controller?.abort();
    if (state.pendingStudentId !== null) unavailable(state.pendingStudentId);
    state.pendingStudentId = null;
    byId('tdDetail').classList.remove('open');
    document.querySelector('.adult-layout').inert = false;
    document.body.style.overflow = state.overflow;
    if (state.previousFocus?.isConnected) state.previousFocus.focus();
  }
  byId('tdDetail').addEventListener('click', event => { if (event.target === byId('tdDetail')) close(); });
  document.addEventListener('keydown', event => {
    if (!byId('tdDetail').classList.contains('open')) return;
    if (event.key === 'Escape') close();
    if (event.key !== 'Tab') return;
    const nodes = [...byId('tdDetail').querySelectorAll('button, a[href], summary, input, select, [tabindex="0"]')].filter(node => node.offsetParent !== null);
    if (!nodes.length) { event.preventDefault(); return; }
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (!byId('tdDetail').contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
    else if (event.shiftKey && (document.activeElement === first || document.activeElement === byId('tdDetailBox'))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || document.activeElement === byId('tdDetailBox'))) { event.preventDefault(); first.focus(); }
  });
  byId('tdSearch').addEventListener('input', filter);
  byId('tdFilter').addEventListener('change', filter);
  byId('tdRosterRetry').addEventListener('click', load);
  byId('tdInviteForm').addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button');
    button.disabled = true;
    try {
      await api('/api/roles/invite', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ student_email: byId('tdInviteEmail').value.trim() }) });
      byId('tdInviteEmail').value = '';
      feedback('Invite sent. The student must approve before you can see their progress.', 'success');
      await load();
    } catch (error) { feedback(error.status === 404 ? 'No student account was found with that email. Check it and try again.' : error.message); }
    finally { button.disabled = false; }
  });
  load();
})();
