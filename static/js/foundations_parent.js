(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { learners: [], current: null, overview: null, busy: false };
  const offset = () => -new Date().getTimezoneOffset();
  const status = (message) => { $('parentStatus').textContent = message || ''; };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || 'Could not load family view. Please try again.');
    return body;
  }

  function renderLearners() {
    const host = $('parentLearners');
    host.replaceChildren();
    state.learners.forEach((learner) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'primer-learner';
      button.textContent = learner.nickname;
      button.setAttribute('aria-current', String(state.current?.id === learner.id));
      button.addEventListener('click', () => selectLearner(learner));
      host.append(button);
    });
  }

  function renderWeek(days) {
    const host = $('parentWeek');
    host.replaceChildren();
    days.forEach((day) => {
      const date = new Date(`${day.date}T12:00:00`);
      const box = document.createElement('div');
      box.className = 'parent-day';
      box.dataset.practiced = String(Boolean(day.answers || day.offline_domain));
      const label = document.createElement('strong');
      label.textContent = date.toLocaleDateString(undefined, { weekday: 'short' });
      const number = document.createElement('span');
      number.textContent = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      const detail = document.createElement('small');
      detail.textContent = [day.answers ? `${day.answers} ${day.answers === 1 ? 'answer' : 'answers'}` : '',
        day.offline_domain ? `${day.offline_domain} together` : ''].filter(Boolean).join(' · ') || 'No practice recorded';
      box.setAttribute('aria-label', `${date.toLocaleDateString()}: ${detail.textContent}`);
      box.append(label, number, detail);
      host.append(box);
    });
  }

  function renderDomains(counts) {
    const host = $('parentDomains');
    host.replaceChildren();
    const max = Math.max(1, ...Object.values(counts));
    Object.entries(counts).forEach(([domain, count]) => {
      const row = document.createElement('div');
      row.className = 'parent-domain';
      const label = document.createElement('span');
      label.textContent = domain;
      const track = document.createElement('div');
      track.className = 'parent-domain-track';
      const fill = document.createElement('span');
      fill.className = 'parent-domain-fill';
      fill.style.width = `${(count / max) * 100}%`;
      track.append(fill);
      const value = document.createElement('output');
      value.textContent = count;
      row.append(label, track, value);
      host.append(row);
    });
  }

  function renderNudge(note, canSend, nextAt) {
    const host = $('parentNudgeCurrent');
    host.replaceChildren();
    host.hidden = !note;
    const form = $('parentNudgeForm');
    form.hidden = Boolean(note && !note.acknowledged_at && !note.withdrawn_at && !note.expired);
    form.querySelector('button').disabled = !canSend;
    const cooldown = $('parentNoteCooldown');
    cooldown.hidden = canSend || form.hidden;
    cooldown.textContent = nextAt ? `Another note can be placed after ${new Date(nextAt).toLocaleString()}.` : '';
    if (!note) return;
    const heading = document.createElement('strong');
    heading.textContent = note.withdrawn_at ? 'Note withdrawn' : note.acknowledged_at ? 'Marked seen in the learner view' : note.expired ? 'Note expired after seven days' : 'Waiting in the learner story';
    const message = document.createElement('p');
    message.textContent = note.message;
    const detail = document.createElement('span');
    detail.textContent = note.practice_after
      ? 'An answered activity was recorded after this note.'
      : 'No answered activity has been recorded since this note.';
    host.append(heading, message, detail);
    if (!note.acknowledged_at && !note.withdrawn_at && !note.expired) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'primer-text-btn';
      button.textContent = 'Withdraw note';
      button.addEventListener('click', () => withdrawNudge(note.id));
      host.append(button);
    }
  }

  function renderSkillMap(data) {
    const host = $('parentSkillMap');
    host.replaceChildren();
    data.skills.forEach((skill) => {
      const row = document.createElement('div');
      row.className = 'primer-skill-row';
      row.dataset.locked = String(!skill.unlocked);
      const title = document.createElement('strong');
      title.textContent = `${skill.domain} / ${skill.title}`;
      const label = document.createElement('span');
      label.textContent = skill.unlocked ? skill.label : 'Later skill';
      const detail = document.createElement('small');
      detail.textContent = skill.unlocked
        ? `${skill.attempts} ${skill.attempts === 1 ? 'try' : 'tries'} · ${skill.independent_correct} correct without an in-app clue`
        : 'Build the previous skill first';
      row.append(title, label, detail);
      host.append(row);
    });
  }

  function render(data, progress) {
    state.overview = data;
    $('parentGrade').value = String(data.learner.grade);
    $('parentLegacyGrade').hidden = data.learner.grade_set;
    renderSkillMap(progress);
    const bridges = Object.entries(progress.scaffolding || {}).filter(([, needed]) => needed).map(([domain]) => domain);
    $('parentScaffold').hidden = bridges.length === 0;
    $('parentScaffold').textContent = bridges.length
      ? `A brief prior-grade bridge is ready in ${bridges.join(', ')} after repeated misses. The story returns to the selected grade afterward.` : '';
    $('parentPracticeDays').textContent = data.practice_days;
    $('parentAnswerCount').textContent = data.total_answers;
    $('parentGoalProgress').textContent = `${data.practice_days} / ${data.weekly_goal}`;
    $('parentLastAnswered').textContent = data.last_answered_at
      ? `Last answered activity: ${new Date(data.last_answered_at).toLocaleString()}`
      : 'No answered activity yet';
    $('parentStoryPosition').textContent = data.journey_complete
      ? 'Story complete · ready to explore again'
      : `Story chapter ${data.chapter} of ${data.chapter_count}`;
    $('parentNudgeTemplate').value = data.suggested_nudge;
    $('parentNudgeSuggestion').textContent = `Suggested because ${data.suggested_nudge_reason.charAt(0).toLowerCase()}${data.suggested_nudge_reason.slice(1)}`;
    $('parentGoal').value = String(data.weekly_goal);
    $('parentFocusSkill').textContent = `${data.focus.domain} / ${data.focus.skill}`;
    $('parentFocusReason').textContent = data.focus.reason;
    $('parentFocusActivity').textContent = data.focus.try_together;
    renderWeek(data.days);
    renderDomains(data.domain_answers);
    renderNudge(data.latest_nudge, data.can_send_nudge, data.next_nudge_at);
    const today = data.days[data.days.length - 1];
    $('parentCheckinForm').querySelector('button').disabled = Boolean(today?.offline_domain);
    $('parentCheckinDomain').disabled = Boolean(today?.offline_domain);
  }

  async function refresh() {
    const learner = state.current;
    if (!learner) return;
    const [data, progress] = await Promise.all([
      api(`/api/primer/learners/${learner.id}/parent?tz_offset_minutes=${offset()}`),
      api(`/api/primer/learners/${learner.id}/progress`),
    ]);
    if (state.current?.id === learner.id) render(data, progress);
  }

  async function selectLearner(learner) {
    state.current = learner;
    renderLearners();
    status(`Loading ${learner.nickname}'s practice…`);
    try { await refresh(); status(''); }
    catch (error) { status(error.message); }
  }

  async function mutate(path, options, success) {
    if (state.busy || !state.current) return;
    state.busy = true;
    status('Saving…');
    try {
      await api(path, options);
      await refresh();
      status(success);
    } catch (error) { status(error.message); }
    finally { state.busy = false; }
  }

  $('parentGoalForm').addEventListener('submit', (event) => {
    event.preventDefault();
    mutate(`/api/primer/learners/${state.current.id}/parent/goal`, {
      method: 'PUT', body: JSON.stringify({ weekly_goal: Number($('parentGoal').value) }),
    }, 'Goal saved. It can change whenever your family needs it.');
  });
  $('parentGradeForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.busy || !state.current) return;
    state.busy = true;
    const learner = state.current;
    status('Saving grade…');
    try {
      const result = await api(`/api/primer/learners/${learner.id}/grade`, {
        method: 'PUT', body: JSON.stringify({ grade: Number($('parentGrade').value) }),
      });
      state.learners = state.learners.map((row) => row.id === learner.id ? result.learner : row);
      if (state.current?.id === learner.id) state.current = result.learner;
      await refresh();
      status('Grade saved. The next activity will use this starting level.');
    } catch (error) { status(error.message); }
    finally { state.busy = false; }
  });
  $('parentCheckinForm').addEventListener('submit', (event) => {
    event.preventDefault();
    mutate(`/api/primer/learners/${state.current.id}/parent/check-in`, {
      method: 'POST', body: JSON.stringify({ domain: $('parentCheckinDomain').value, tz_offset_minutes: offset() }),
    }, 'Together time recorded for today.');
  });
  $('parentNudgeForm').addEventListener('submit', (event) => {
    event.preventDefault();
    mutate(`/api/primer/learners/${state.current.id}/parent/nudge`, {
      method: 'POST', body: JSON.stringify({ template_id: $('parentNudgeTemplate').value }),
    }, 'The note is waiting in the learner story.');
  });
  function withdrawNudge(id) {
    mutate(`/api/primer/learners/${state.current.id}/parent/nudge`, {
      method: 'DELETE', body: JSON.stringify({ id }),
    }, 'Note withdrawn from the learner story.');
  }

  $('parentPrint').addEventListener('click', () => window.print());
  $('parentDelete').addEventListener('click', async () => {
    if (state.busy || !state.current) return;
    const learner = state.current;
    if (!window.confirm(`Remove ${learner.nickname} and all Foundations progress from this account?`)) return;
    state.busy = true;
    try {
      await api(`/api/primer/learners/${learner.id}`, { method: 'DELETE' });
      state.learners = state.learners.filter((row) => row.id !== learner.id);
      state.current = null;
      $('parentEmpty').hidden = Boolean(state.learners.length);
      $('parentWorkspace').hidden = !state.learners.length;
      if (state.learners.length) await selectLearner(state.learners[0]);
      else status('Learner and progress removed.');
    } catch (error) { status(error.message); }
    finally { state.busy = false; }
  });

  api('/api/primer/learners').then((data) => {
    state.learners = data.learners || [];
    $('parentEmpty').hidden = Boolean(state.learners.length);
    $('parentWorkspace').hidden = !state.learners.length;
    if (state.learners.length) return selectLearner(state.learners[0]);
    status('');
  }).catch((error) => status(error.message));
})();
