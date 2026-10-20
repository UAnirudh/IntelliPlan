(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { learners: [], current: null, activity: null, progress: null, familyNoteId: null, busy: false,
    viewVersion: 0, activityRequest: 0, progressRequest: 0,
    session: { answered: 0, independent: 0, assisted: 0, revisits: 0 }, goal: 3 };
  const status = (message, retry = false) => {
    $('primerStatus').textContent = message || '';
    $('primerRetry').hidden = !retry;
  };
  const currentView = () => ({ version: state.viewVersion, learnerId: state.current?.id, grade: state.current?.grade });
  const isCurrentView = (view) => view.version === state.viewVersion
    && view.learnerId === state.current?.id && view.grade === state.current?.grade;

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(path, {
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        ...options,
        signal: controller.signal,
      });
      const body = await response.json();
      if (!response.ok) {
        const error = new Error(body.error || 'Something went wrong. Please try again.');
        error.status = response.status;
        throw error;
      }
      return body;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The connection took too long. Try again; your saved practice is still available.');
      if (error.name === 'SyntaxError') throw new Error('The practice response could not be read. Please try again.');
      throw error;
    } finally { window.clearTimeout(timeout); }
  }

  async function recoverStale(error, learnerId) {
    if (![409, 410].includes(error.status) || !state.current || state.current.id !== learnerId) return false;
    if (await loadActivity(learnerId)) status('This chapter changed or expired. The current step is ready.');
    return true;
  }

  function showStart(show) {
    $('primerStart').hidden = !show;
    $('primerWorkspace').hidden = show;
    $('primerCancelCreate').hidden = !state.current;
    if (show) $('primerNickname').focus();
  }

  function renderSession() {
    const session = state.session;
    $('primerSessionSummary').textContent = session.answered
      ? `${session.answered} of ${state.goal} questions checked · ${session.independent} correct independently`
      : `Try ${state.goal} questions at your own pace. Your next step is saved as you go.`;
    const paused = session.answered >= state.goal;
    $('primerSessionEnd').hidden = !paused;
    $('primerSessionResult').textContent = `${session.answered} questions checked: ${session.independent} correct independently, ${session.assisted} with a clue, and ${session.revisits} to revisit.`;
  }

  function renderRecommendation() {
    const data = state.activity;
    if (!data?.skill) return;
    const skill = state.progress?.skills?.find((row) => row.id === data.skill.id);
    $('primerRecommendation').textContent = data.story.repair
      ? 'A fresh question on the same skill helps you try the feedback for yourself.'
      : !skill || !skill.attempts
        ? `Start with ${data.skill.title.toLowerCase()}. Try it independently before opening a clue.`
        : `${skill.independent_correct} correct independently on ${data.skill.title.toLowerCase()}. Try this question before opening a clue.`;
  }

  function renderProgress(data) {
    state.progress = data;
    $('primerPracticeCount').textContent = `· ${data.total_attempts} checked answers saved across grades`;
    $('primerProgressStatus').textContent = '';
    $('primerProgressRetry').hidden = true;
    const skills = data.skills || [];
    const ready = skills.filter((skill) => skill.unlocked);
    const due = ready.filter((skill) => skill.attempts && skill.due_at && new Date(skill.due_at) <= new Date());
    $('primerPathSummary').textContent = `${ready.length} of ${skills.length} skills open at this practice level · ${due.length} ready for review. Later skills open after two correct answers without a clue on their prerequisite.`;
    const focus = data.focus;
    $('primerOfflinePractice').hidden = !focus?.try_together;
    $('primerOfflineTitle').textContent = focus ? `${focus.domain === 'Arithmetic' ? 'Math' : focus.domain} · ${focus.skill}` : '';
    $('primerOfflineReason').textContent = focus?.reason || '';
    $('primerOfflineTask').textContent = focus?.try_together || '';
    const host = $('primerSkillMap');
    host.replaceChildren();
    for (const domain of ['Reading', 'Writing', 'Arithmetic']) {
      const section = document.createElement('section');
      const title = document.createElement('h3');
      title.textContent = domain === 'Arithmetic' ? 'Math' : domain;
      section.appendChild(title);
      (data.skills || []).filter((skill) => skill.domain === domain).forEach((skill) => {
        const row = document.createElement('div');
        row.className = 'primer-skill-row';
        row.dataset.locked = String(!skill.unlocked);
        const label = document.createElement('strong');
        label.textContent = skill.title;
        const stage = document.createElement('span');
        stage.textContent = !skill.unlocked ? 'Earlier practice needed' : !skill.attempts ? 'Ready to try'
          : due.some((row) => row.id === skill.id) ? 'Review ready' : skill.label;
        const evidence = document.createElement('small');
        evidence.textContent = `${skill.attempts} checked · ${skill.independent_correct} correct independently`;
        row.append(label, stage, evidence);
        if (!skill.unlocked) {
          const next = document.createElement('small');
          next.textContent = 'Keep practicing earlier skills independently to open this step.';
          row.appendChild(next);
        } else if (skill.due_at && skill.attempts && !due.some((item) => item.id === skill.id)) {
          const review = document.createElement('small');
          review.textContent = `Next review: ${new Date(skill.due_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`;
          row.appendChild(review);
        }
        section.appendChild(row);
      });
      host.appendChild(section);
    }
    renderRecommendation();
  }

  async function loadProgress(learnerId) {
    const view = currentView();
    const request = ++state.progressRequest;
    try {
      const data = await api(`/api/primer/learners/${learnerId}/progress`);
      if (isCurrentView(view) && request === state.progressRequest && data.grade === view.grade) renderProgress(data);
    } catch (error) {
      if (isCurrentView(view) && request === state.progressRequest) {
        $('primerProgressStatus').textContent = `Your practice path could not refresh. ${error.message}`;
        $('primerProgressRetry').hidden = false;
      }
    }
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
      button.addEventListener('click', () => { if (state.current?.id !== learner.id || !state.activity) selectLearner(learner); });
      host.appendChild(button);
    });
  }

  function renderStory(data) {
    const story = data.story;
    $('primerWorldLabel').textContent = data.world_name;
    $('primerGradeLabel').textContent = data.learner.grade === 0 ? 'Kindergarten' : data.learner.grade === 13 ? 'College foundation' : `Grade ${data.learner.grade}`;
    const older = data.learner.grade >= 9;
    $('primerPageTitle').textContent = older ? 'Think it through. Build your skills.' : 'Small practice. Real understanding.';
    $('primerPageIntro').textContent = older
      ? 'Investigate a case while you practice reading evidence, precise writing, and quantitative reasoning. Each checked answer shapes what comes next.'
      : 'Read carefully, write clearly, and reason with numbers. A story connects the questions; your practice helps choose the next step.';
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
    $('primerEvidenceNote').hidden = true;
    $('primerSessionEnd').hidden = true;
    $('primerNext').hidden = true;
    $('primerClue').hidden = true;
    $('primerClue').textContent = '';
    $('primerShowClue').hidden = false;
    $('primerAnswer').hidden = false;
    $('primerCard').hidden = false;
    renderRecommendation();
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
        if (await loadActivity(learnerId)) { status(''); focusStep(); }
      }
    } catch (error) {
      try {
        if (!(await recoverStale(error, learnerId))) status(error.message);
      } catch (refreshError) { status(refreshError.message); }
      $('primerChoiceOptions').querySelectorAll('button').forEach((button) => { button.disabled = false; });
    } finally { state.busy = false; }
  }

  async function loadActivity(learnerId) {
    const view = currentView();
    const request = ++state.activityRequest;
    if (view.learnerId !== learnerId) return false;
    try {
      const data = await api(`/api/primer/learners/${learnerId}/activity`);
      if (!isCurrentView(view) || request !== state.activityRequest
          || data.learner?.id !== learnerId || data.learner.grade !== view.grade) return false;
      renderActivity(data);
      return true;
    } catch (error) {
      if (!isCurrentView(view) || request !== state.activityRequest) return false;
      throw error;
    }
  }

  function focusStep() {
    const target = !$('primerCard').hidden ? $('primerPrompt')
      : !$('primerStoryChoice').hidden ? $('primerChoiceQuestion') : $('primerStoryEnd').querySelector('h3');
    if (target) { target.tabIndex = -1; target.focus({ preventScroll: true }); target.scrollIntoView({ block: 'nearest' }); }
  }

  async function refreshFamilyNote() {
    if (!state.current || document.hidden) return;
    const learnerId = state.current.id;
    const view = currentView();
    try {
      const data = await api(`/api/primer/learners/${learnerId}/nudge`);
      if (!isCurrentView(view)) return;
      state.familyNoteId = data.nudge?.id || null;
      $('primerFamilyMessage').textContent = data.nudge?.message || '';
      $('primerFamilyNote').hidden = !state.familyNoteId;
    } catch (_) { /* The current activity remains usable if note refresh fails. */ }
  }

  async function selectLearner(learner) {
    if (state.busy) return;
    state.viewVersion += 1;
    state.current = learner;
    const view = currentView();
    state.activity = null;
    state.progress = null;
    state.session = { answered: 0, independent: 0, assisted: 0, revisits: 0 };
    $('primerPlacementGrade').value = learner.grade_set ? String(learner.grade) : '';
    $('primerSkillMap').replaceChildren();
    $('primerPracticeCount').textContent = '';
    $('primerPathSummary').textContent = '';
    $('primerOfflinePractice').hidden = true;
    $('primerProgressStatus').textContent = '';
    $('primerProgressRetry').hidden = true;
    renderSession();
    showStart(false);
    renderLearners();
    $('primerCard').hidden = true;
    $('primerScene').hidden = true;
    $('primerStoryChoice').hidden = true;
    $('primerStoryEnd').hidden = true;
    $('primerStoryHistory').hidden = true;
    $('primerStoryHistoryList').replaceChildren();
    $('primerFamilyNote').hidden = true;
    $('primerFamilyMessage').textContent = '';
    state.familyNoteId = null;
    if (!learner.grade_set) {
      $('primerActivityTitle').textContent = 'Choose your starting point.';
      $('primerActivityIntro').textContent = 'Set the grade you want to practice before starting a question.';
      $('primerGradeLabel').textContent = 'Choose a practice grade';
      $('primerWorldLabel').textContent = '';
      $('primerChapterTrack').replaceChildren();
      $('primerPlacement').closest('details').open = true;
      status('Choose a practice grade in Practice settings to begin at a useful level.');
      return;
    }
    status('Loading the next activity…');
    void loadProgress(learner.id);
    try {
      if (await loadActivity(learner.id)) status('');
    } catch (error) { if (isCurrentView(view)) status(error.message, true); }
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
      state.busy = false;
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
      const assisted = Boolean(result.evidence?.clue_used);
      $('primerEvidenceNote').textContent = assisted
        ? 'Clue used · This is assisted practice. Try a fresh question without a clue to build independent evidence.'
        : result.correct ? 'Correct independently · This checked answer is saved as practice evidence.'
          : 'A useful next step · Read the feedback, then try a fresh question on this skill.';
      $('primerEvidenceNote').hidden = false;
      state.session.answered += 1;
      if (result.correct && !assisted) state.session.independent += 1;
      else if (result.correct) state.session.assisted += 1;
      else state.session.revisits += 1;
      renderSession();
      void loadProgress(learnerId);
      $('primerAnswer').hidden = true;
      $('primerNext').hidden = false;
      $('primerNext').firstChild.textContent = result.retry ? 'Try a fresh question ' : 'Continue practice ';
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
    state.busy = true;
    $('primerNext').disabled = true;
    status('Loading the next activity…');
    try {
      if (!(await loadActivity(state.current.id))) return;
      if (state.session.answered >= state.goal) state.session = { answered: 0, independent: 0, assisted: 0, revisits: 0 };
      renderSession();
      status(''); focusStep();
    }
    catch (error) { status(error.message); }
    finally { state.busy = false; $('primerNext').disabled = false; }
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
        if (await loadActivity(learnerId)) status('');
      }
    } catch (error) {
      try {
        if (!(await recoverStale(error, learnerId))) status(error.message);
      } catch (refreshError) { status(refreshError.message); }
    }
    finally { $('primerRestart').disabled = false; state.busy = false; }
  });
  $('primerAdd').addEventListener('click', () => { if (!state.busy) { status(''); showStart(true); } });
  $('primerCancelCreate').addEventListener('click', () => showStart(false));
  $('primerSessionGoal').addEventListener('change', () => {
    state.goal = Number($('primerSessionGoal').value);
    renderSession();
  });
  $('primerProgressRetry').addEventListener('click', () => {
    if (state.current?.grade_set) void loadProgress(state.current.id);
  });
  $('primerRetry').addEventListener('click', async () => {
    if (state.busy) return;
    if (!state.current) { void initialize(); return; }
    if (!state.current.grade_set) return;
    state.busy = true;
    status('Loading the next activity…');
    try {
      if (await loadActivity(state.current.id)) { status(''); focusStep(); }
    } catch (error) { status(error.message, true); }
    finally { state.busy = false; }
  });
  $('primerPlacement').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!state.current || state.busy) return;
    const learnerId = state.current.id;
    state.busy = true;
    const button = $('primerPlacement').querySelector('button');
    button.disabled = true;
    try {
      const data = await api(`/api/primer/learners/${learnerId}/grade`, {
        method: 'PUT', body: JSON.stringify({ grade: Number($('primerPlacementGrade').value) }),
      });
      const index = state.learners.findIndex((learner) => learner.id === learnerId);
      if (index !== -1) state.learners[index] = data.learner;
      state.busy = false;
      $('primerPlacement').closest('details').open = false;
      await selectLearner(data.learner);
    } catch (error) { status(error.message); }
    finally { state.busy = false; button.disabled = false; }
  });
  window.setInterval(refreshFamilyNote, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshFamilyNote(); });

  async function initialize() {
    status('Loading your learners…');
    try {
      const data = await api('/api/primer/learners');
      state.learners = data.learners;
      if (state.learners.length) await selectLearner(state.learners[0]);
      else { showStart(true); status(''); }
    } catch (error) { status(error.message, true); }
  }
  void initialize();
})();
