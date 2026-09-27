(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { learners: [], current: null, activity: null, familyNoteId: null, busy: false };
  const status = (message) => { $('primerStatus').textContent = message || ''; };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.error || 'Something went wrong. Please try again.');
      error.status = response.status;
      throw error;
    }
    return body;
  }

  async function recoverStale(error, learnerId) {
    if (![409, 410].includes(error.status) || !state.current || state.current.id !== learnerId) return false;
    await loadActivity(learnerId);
    status('This chapter changed or expired. The current step is ready.');
    return true;
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

  function renderStory(data) {
    const story = data.story;
    $('primerWorldLabel').textContent = data.world_name;
    $('primerGradeLabel').textContent = data.learner.grade === 0 ? 'Kindergarten' : data.learner.grade === 13 ? 'College foundation' : `Grade ${data.learner.grade}`;
    const older = data.learner.grade >= 9;
    $('primerPageTitle').textContent = older ? 'A case that evolves with you.' : 'A story that remembers.';
    $('primerPageIntro').textContent = older
      ? 'Investigate a changing case, test claims against evidence, write with precision, and work through quantitative decisions at your level.'
      : 'Explore a world, make choices that carry forward, and practice reading, writing, and math at a level chosen for you.';
    $('primerChapterKicker').textContent = older ? 'TODAY\'S CASE' : 'TODAY\'S CHAPTER';
    $('primerActivityIntro').textContent = older
      ? 'Each case combines a source, a writing decision, and a quantitative problem before you choose what happens next.'
      : 'A chapter has three clues and one story choice. Each clue practices a different skill.';
    $('primerActivityTitle').textContent = story.title;
    $('primerScene').hidden = false;
    $('primerStoryScene').textContent = story.scene;
    $('primerStoryMemory').hidden = !story.previous_choice;
    $('primerStoryMemory').textContent = story.previous_choice || '';
    const track = $('primerChapterTrack');
    track.replaceChildren();
    for (let index = 1; index <= story.chapter_count; index += 1) {
      const marker = document.createElement('span');
      marker.className = 'primer-chapter-marker';
      marker.dataset.state = story.complete || index < story.chapter ? 'done' : index === story.chapter ? 'current' : 'upcoming';
      marker.textContent = `Chapter ${index}`;
      track.appendChild(marker);
    }
    const history = $('primerStoryHistory');
    const historyList = $('primerStoryHistoryList');
    historyList.replaceChildren();
    history.hidden = !story.history || !story.history.length;
    (story.history || []).forEach((entry) => {
      const row = document.createElement('li');
      const title = document.createElement('strong');
      title.textContent = `${entry.chapter}: ${entry.choice}`;
      const consequence = document.createElement('span');
      consequence.textContent = entry.consequence;
      row.append(title, consequence);
      historyList.appendChild(row);
    });
    $('primerCard').hidden = true;
    $('primerStoryChoice').hidden = true;
    $('primerStoryEnd').hidden = true;
    if (story.complete) {
      $('primerStoryEnd').hidden = false;
      return;
    }
    if (story.awaiting_choice) {
      $('primerStoryChoice').hidden = false;
      $('primerChoiceQuestion').textContent = story.question;
      const host = $('primerChoiceOptions');
      host.replaceChildren();
      story.choices.forEach((choice) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'primer-path-button';
        button.textContent = choice.label;
        button.addEventListener('click', () => chooseStory(choice.id));
        host.appendChild(button);
      });
    }
  }

  function renderActivity(data) {
    state.activity = data;
    state.familyNoteId = data.family_note?.id || null;
    $('primerFamilyNote').hidden = !state.familyNoteId;
    $('primerFamilyMessage').textContent = data.family_note?.message || '';
    renderStory(data);
    if (!data.item) return;
    $('primerBeatTitle').textContent = `${data.story.beat_title} · ${data.story.beat} of ${data.story.beat_count}`;
    $('primerBeatIntro').textContent = data.story.beat_intro;
    $('primerSkill').textContent = `${data.skill.domain.toUpperCase()} / ${data.skill.title}`;
    $('primerPrompt').textContent = data.item.prompt;
    $('primerFeedback').hidden = true;
    $('primerNext').hidden = true;
    $('primerClue').hidden = true;
    $('primerClue').textContent = '';
    $('primerShowClue').hidden = false;
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
      input.setAttribute('aria-label', data.skill.domain === 'Arithmetic' ? 'Type your number or fraction' : 'Type your answer');
      host.appendChild(input);
    }
  }

  async function chooseStory(choice) {
    if (state.busy || !state.current || !state.activity) return;
    state.busy = true;
    const learnerId = state.current.id;
    $('primerChoiceOptions').querySelectorAll('button').forEach((button) => { button.disabled = true; });
    status('Opening the next chapter…');
    try {
      await api(`/api/primer/learners/${learnerId}/choice`, {
        method: 'POST', body: JSON.stringify({ token: state.activity.token, choice }),
      });
      if (state.current && state.current.id === learnerId) {
        await loadActivity(learnerId);
        status('');
      }
    } catch (error) {
      try {
        if (!(await recoverStale(error, learnerId))) status(error.message);
      } catch (refreshError) { status(refreshError.message); }
      $('primerChoiceOptions').querySelectorAll('button').forEach((button) => { button.disabled = false; });
    } finally { state.busy = false; }
  }

  async function loadActivity(learnerId) {
    const data = await api(`/api/primer/learners/${learnerId}/activity`);
    if (state.current && state.current.id === learnerId) renderActivity(data);
  }

  async function refreshFamilyNote() {
    if (!state.current || document.hidden) return;
    const learnerId = state.current.id;
    try {
      const data = await api(`/api/primer/learners/${learnerId}/nudge`);
      if (state.current?.id !== learnerId) return;
      state.familyNoteId = data.nudge?.id || null;
      $('primerFamilyMessage').textContent = data.nudge?.message || '';
      $('primerFamilyNote').hidden = !state.familyNoteId;
    } catch (_) { /* The current activity remains usable if note refresh fails. */ }
  }

  async function selectLearner(learner) {
    state.current = learner;
    showStart(false);
    renderLearners();
    $('primerCard').hidden = true;
    status('Loading the next activity…');
    try {
      await loadActivity(learner.id);
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
        body: JSON.stringify({ nickname: $('primerNickname').value.trim(), world: $('primerWorld').value,
          grade: Number($('primerGrade').value) }),
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
    const learnerId = state.current.id;
    $('primerCheck').disabled = true;
    status('');
    try {
      const result = await api(`/api/primer/learners/${learnerId}/answer`, {
        method: 'POST', body: JSON.stringify({ token: state.activity.token, answer: selected.value }),
      });
      if (!state.current || state.current.id !== learnerId) return;
      $('primerFeedback').dataset.correct = String(result.correct);
      $('primerFeedback').textContent = `${result.correct ? 'You got it. ' : 'Good try. '}${result.feedback}`;
      $('primerFeedback').hidden = false;
      $('primerAnswer').hidden = true;
      $('primerNext').hidden = false;
      $('primerNext').firstChild.textContent = result.retry ? 'Try another clue ' : 'Continue story ';
      $('primerNext').focus();
    } catch (error) {
      try {
        if (!(await recoverStale(error, learnerId))) status(error.message);
      } catch (refreshError) { status(refreshError.message); }
    }
    finally { $('primerCheck').disabled = false; state.busy = false; }
  });

  $('primerNext').addEventListener('click', async () => {
    if (!state.current || state.busy) return;
    status('Loading the next activity…');
    try { await loadActivity(state.current.id); status(''); }
    catch (error) { status(error.message); }
  });
  $('primerFamilyAcknowledge').addEventListener('click', async () => {
    if (!state.current || !state.familyNoteId || state.busy) return;
    const learnerId = state.current.id;
    const noteId = state.familyNoteId;
    state.busy = true;
    $('primerFamilyAcknowledge').disabled = true;
    try {
      await api(`/api/primer/learners/${learnerId}/nudge/${noteId}/acknowledge`, { method: 'POST' });
      if (state.current?.id === learnerId) {
        state.familyNoteId = null;
        $('primerFamilyNote').hidden = true;
        status('Your grown-up will know you saw the note.');
      }
    } catch (error) { status(error.message); }
    finally { $('primerFamilyAcknowledge').disabled = false; state.busy = false; }
  });
  $('primerShowClue').addEventListener('click', async () => {
    if (!state.current || !state.activity || state.busy) return;
    state.busy = true;
    const learnerId = state.current.id;
    $('primerShowClue').disabled = true;
    try {
      const data = await api(`/api/primer/learners/${learnerId}/hint`, {
        method: 'POST', body: JSON.stringify({ token: state.activity.token }),
      });
      if (state.current && state.current.id === learnerId) {
        $('primerClue').textContent = data.hint;
        $('primerClue').hidden = false;
        $('primerShowClue').hidden = true;
        status('');
      }
    } catch (error) {
      try {
        if (!(await recoverStale(error, learnerId))) status(error.message);
      } catch (refreshError) { status(refreshError.message); }
    }
    finally { $('primerShowClue').disabled = false; state.busy = false; }
  });
  $('primerRestart').addEventListener('click', async () => {
    if (!state.current || state.busy) return;
    const learnerId = state.current.id;
    state.busy = true;
    $('primerRestart').disabled = true;
    status('Starting a new journey…');
    try {
      await api(`/api/primer/learners/${learnerId}/journey/restart`, { method: 'POST' });
      if (state.current && state.current.id === learnerId) {
        await loadActivity(learnerId);
        status('');
      }
    } catch (error) {
      try {
        if (!(await recoverStale(error, learnerId))) status(error.message);
      } catch (refreshError) { status(refreshError.message); }
    }
    finally { $('primerRestart').disabled = false; state.busy = false; }
  });
  $('primerAdd').addEventListener('click', () => { status(''); showStart(true); });
  window.setInterval(refreshFamilyNote, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshFamilyNote(); });

  api('/api/primer/learners').then((data) => {
    state.learners = data.learners;
    if (state.learners.length) return selectLearner(state.learners[0]);
    showStart(true);
    status('');
  }).catch((error) => status(error.message));
})();
