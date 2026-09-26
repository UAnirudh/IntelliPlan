(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { learners: [], current: null, activity: null, busy: false };
  const status = (message) => { $('primerStatus').textContent = message || ''; };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || 'Something went wrong. Please try again.');
    return body;
  }

  function showStart(show) {
    $('primerStart').hidden = !show;
    $('primerWorkspace').hidden = show;
    if (show) $('primerNickname').focus();
  }

  function renderLearners() {
    const host = $('primerLearners');
    host.replaceChildren();
    state.learners.forEach((learner) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'primer-learner';
      button.textContent = learner.nickname;
      button.setAttribute('aria-current', String(state.current && state.current.id === learner.id));
      button.addEventListener('click', () => selectLearner(learner));
      host.appendChild(button);
    });
  }

  function renderProgress(data) {
    $('primerPrintName').textContent = data.learner.nickname;
    $('primerAttemptCount').textContent = `${data.total_attempts} ${data.total_attempts === 1 ? 'answer' : 'answers'}`;
    const host = $('primerSkillMap');
    host.replaceChildren();
    data.skills.forEach((skill) => {
      const row = document.createElement('div');
      row.className = 'primer-skill-row';
      row.dataset.locked = String(!skill.unlocked);
      const title = document.createElement('strong');
      title.textContent = skill.title;
      const label = document.createElement('span');
      label.textContent = skill.unlocked ? skill.label : 'Next chapter';
      const detail = document.createElement('small');
      detail.textContent = skill.unlocked ? `${skill.domain} · ${skill.attempts} ${skill.attempts === 1 ? 'try' : 'tries'}` : `${skill.domain} · build the previous skill first`;
      row.append(title, label, detail);
      host.appendChild(row);
    });
  }

  function renderActivity(data) {
    state.activity = data;
    $('primerWorldLabel').textContent = data.world_name;
    $('primerSkill').textContent = `${data.skill.domain.toUpperCase()} / ${data.skill.title}`;
    $('primerPrompt').textContent = data.item.prompt;
    $('primerFeedback').hidden = true;
    $('primerNext').hidden = true;
    $('primerAnswer').hidden = false;
    $('primerCard').hidden = false;
    const host = $('primerResponse');
    host.replaceChildren();
    if (data.item.response_kind === 'choice') {
      const choices = document.createElement('div');
      choices.className = 'primer-choices';
      data.item.options.forEach((option) => {
        const label = document.createElement('label');
        label.className = 'primer-choice';
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'primerChoice';
        radio.value = option;
        radio.required = true;
        label.append(radio, document.createTextNode(option));
        choices.appendChild(label);
      });
      host.appendChild(choices);
    } else {
      const input = document.createElement('input');
      input.className = 'primer-answer-text';
      input.id = 'primerTextAnswer';
      input.type = 'text';
      input.maxLength = 200;
      input.required = true;
      input.autocomplete = 'off';
      input.setAttribute('aria-label', 'Type your sentence');
      host.appendChild(input);
    }
  }

  async function loadActivity(learnerId) {
    const data = await api(`/api/primer/learners/${learnerId}/activity`);
    if (state.current && state.current.id === learnerId) renderActivity(data);
  }

  async function loadProgress(learnerId) {
    const data = await api(`/api/primer/learners/${learnerId}/progress`);
    if (state.current && state.current.id === learnerId) renderProgress(data);
  }

  async function selectLearner(learner) {
    state.current = learner;
    showStart(false);
    renderLearners();
    $('primerCard').hidden = true;
    status('Loading the next activity…');
    try {
      await Promise.all([loadProgress(learner.id), loadActivity(learner.id)]);
      status('');
    } catch (error) { status(error.message); }
  }

  $('primerCreate').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.busy) return;
    state.busy = true;
    const button = $('primerCreate').querySelector('button');
    button.disabled = true;
    try {
      const data = await api('/api/primer/learners', {
        method: 'POST',
        body: JSON.stringify({ nickname: $('primerNickname').value.trim(), world: $('primerWorld').value }),
      });
      state.learners.push(data.learner);
      $('primerCreate').reset();
      await selectLearner(data.learner);
    } catch (error) { status(error.message); }
    finally { button.disabled = false; state.busy = false; }
  });

  $('primerAnswer').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.busy || !state.activity) return;
    const selected = $('primerResponse').querySelector('input:checked, input[type="text"]');
    if (!selected || !selected.value.trim()) { status('Choose or type an answer.'); return; }
    state.busy = true;
    $('primerCheck').disabled = true;
    status('');
    try {
      const learnerId = state.current.id;
      const result = await api(`/api/primer/learners/${learnerId}/answer`, {
        method: 'POST', body: JSON.stringify({ token: state.activity.token, answer: selected.value }),
      });
      if (!state.current || state.current.id !== learnerId) return;
      $('primerFeedback').dataset.correct = String(result.correct);
      $('primerFeedback').textContent = `${result.correct ? 'You got it. ' : 'Good try. '}${result.feedback}`;
      $('primerFeedback').hidden = false;
      $('primerAnswer').hidden = true;
      $('primerNext').hidden = false;
      await loadProgress(learnerId);
      $('primerNext').focus();
    } catch (error) { status(error.message); }
    finally { $('primerCheck').disabled = false; state.busy = false; }
  });

  $('primerNext').addEventListener('click', async () => {
    if (!state.current || state.busy) return;
    status('Loading the next activity…');
    try { await loadActivity(state.current.id); status(''); }
    catch (error) { status(error.message); }
  });
  $('primerAdd').addEventListener('click', () => { status(''); showStart(true); });
  $('primerPrint').addEventListener('click', () => window.print());
  $('primerDelete').addEventListener('click', async () => {
    if (!state.current || state.busy) return;
    const learner = state.current;
    if (!window.confirm(`Remove ${learner.nickname} and all Foundations progress from this account?`)) return;
    state.busy = true;
    try {
      await api(`/api/primer/learners/${learner.id}`, { method: 'DELETE' });
      state.learners = state.learners.filter((row) => row.id !== learner.id);
      state.current = null;
      state.activity = null;
      if (state.learners.length) await selectLearner(state.learners[0]);
      else { showStart(true); status('Learner and progress removed.'); }
    } catch (error) { status(error.message); }
    finally { state.busy = false; }
  });

  api('/api/primer/learners').then((data) => {
    state.learners = data.learners;
    if (state.learners.length) return selectLearner(state.learners[0]);
    showStart(true);
    status('');
  }).catch((error) => status(error.message));
})();
