'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const status = message => { $('hub-status').textContent = message; };
  async function api(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', ...options,
      headers: {'Content-Type': 'application/json', ...options.headers}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || data.error || `Request failed (${response.status}).`);
    return data;
  }
  const post = (path, data) => api(path, {method: 'POST', body: JSON.stringify(data)});
  function node(tag, text, parent) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (parent) parent.append(element);
    return element;
  }
  function button(label, parent, action) {
    const b = node('button', label, parent); b.type = 'button';
    b.addEventListener('click', () => run(b, action)); return b;
  }
  async function run(control, action) {
    control.disabled = true;
    try { await action(); } catch (error) { status(error.message); }
    finally { control.disabled = false; }
  }
  const messages = []; let conversationId, sittingId, started, timer, courseRequest = 0;
  function showSitting(data) {
    sittingId = data.id; started = Date.now();
    $('sitting-controls').hidden = data.state === 'completed';
    $('sitting-objective').value = data.objective;
    clearInterval(timer);
    const tick = () => {
      const seconds = data.elapsed_seconds + (data.state === 'active' ? (Date.now() - started) / 1000 : 0);
      $('sitting-time').textContent = `Elapsed time: ${Math.floor(seconds / 60)} minutes · ${data.state} · reported time`;
    };
    tick(); if (data.state === 'active') timer = setInterval(tick, 1000);
  }
  async function restoreSitting() {
    const data = await api('/api/learning/sessions');
    const open = data.sessions.find(s => ['active','paused'].includes(s.state));
    if (open) { showSitting(open); status('Your unfinished study session is ready to resume or finish.'); }
  }
  document.querySelectorAll('[data-activity]').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('[data-activity]').forEach(tab => tab.setAttribute('aria-pressed', String(tab === b)));
    document.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== b.dataset.activity; });
    if (b.dataset.activity === 'progress') loadProgress().catch(e => status(e.message));
    if (b.dataset.activity === 'courses') loadLibrary().catch(e => status(e.message));
  }));
  $('hub-tutor-form').addEventListener('submit', event => {
    event.preventDefault();
    run(event.submitter, async () => {
      const question = $('hub-question').value.trim(); if (!question) return;
      const activity = $('tutor-activity').value;
      messages.push({role: 'user', content: `${activity === 'practice' ? 'Guide me with hints and ask me to reason. ' : activity === 'review' ? 'Help me retrieve and review this concept. ' : 'Explain clearly, then check my understanding. '}${question}`});
      node('p', question, $('hub-conversation')).className = 'learning-message';
      const data = await post('/api/tutor', {messages: messages.slice(-16), conversation_id: conversationId});
      messages.push({role: 'assistant', content: data.reply}); conversationId = data.conversation_id;
      node('p', data.reply, $('hub-conversation')).className = 'learning-message';
      $('hub-question').value = ''; status(data.assessment_protection ? 'Your school’s assessment protection is active.' : '');
    });
  });
  $('hub-sitting-form').addEventListener('submit', event => {
    event.preventDefault(); run(event.submitter, async () => {
      const data = await post('/api/learning/sessions', {objective: $('sitting-objective').value,
        planned_minutes: Number($('sitting-minutes').value), method: $('sitting-method').value,
        intervention: $('sitting-intervention').value});
      showSitting(data); status('Session started. Choose a checkpoint when you have worked on your goal.');
    });
  });
  document.querySelectorAll('[data-checkpoint]').forEach(b => b.addEventListener('click', () => run(b, async () => {
    if (!sittingId) return;
    const data = await post(`/api/learning/sessions/${sittingId}/checkpoint`, {kind: b.dataset.checkpoint});
    status(`${data.message}${data.intervention !== 'none' ? ` Selected reset: ${data.intervention}.` : ''}`);
    showSitting(data.session);
  })));
  async function loadProgress() {
    const data = await api('/api/learning/progress'); const panel = $('hub-progress'); panel.replaceChildren();
    node('p', data.note, panel);
    for (const review of data.due_review || []) node('p', `Review due: ${review.objective}. Try a different problem, then explain the concept to the tutor.`, panel);
    if (!data.evidence.length) node('p', 'Your new study activities and reviewed checks will appear here. Existing tutor progress remains in your practice path.', panel);
    for (const e of data.evidence) node('p', `${e.objective} · ${e.correct === null ? 'Reported activity' : e.correct ? 'Reviewed check passed' : 'Practice needs review'}${e.assisted ? ' · assisted or answer-key access' : ''}`, panel);
  }
  $('hub-course-form').addEventListener('submit', event => {
    event.preventDefault(); run(event.submitter, async () => {
      const data = await post('/api/learning/courses', {goal: $('course-goal').value, mode: $('course-mode').value,
        confirm_provider_use: $('course-provider-confirm').checked});
      await openCourse(data.id); await loadLibrary(); status('Outline saved. Review the sequence before starting generation.');
    });
  });
  async function loadLibrary() {
    const data = await api('/api/learning/courses'); const panel = $('course-library'); panel.replaceChildren();
    for (const course of data.courses) button(`${course.title} · ${course.state}`, panel, () => openCourse(course.id));
  }
  async function openCourse(id) {
    const requestId = ++courseRequest;
    const course = await api(`/api/learning/courses/${id}`);
    if (requestId !== courseRequest) return;
    const review = course.state !== 'reviewed' ? await api(`/api/learning/courses/${id}/review`) : null;
    if (requestId !== courseRequest) return;
    const panel = $('course-workspace'); panel.replaceChildren();
    node('h3', course.blueprint.title, panel); node('p', course.blueprint.overview, panel);
    node('p', `Next: ${course.next.key || 'Review'} · ${course.next.reason}`, panel);
    const fields = [];
    for (const spec of course.blueprint.lessons) {
      const label = node('label', `${spec.unit} · ${spec.minutes} minutes`, panel);
      const input = node('input', undefined, label); input.value = spec.title; input.maxLength = 200;
      input.disabled = course.state !== 'draft'; fields.push([spec, input]);
    }
    if (course.state === 'draft') button('Save outline edits', panel, async () => {
      for (const [spec, input] of fields) spec.title = input.value;
      await api(`/api/learning/courses/${id}`, {method: 'PUT', body: JSON.stringify({blueprint: course.blueprint})});
      if (requestId === courseRequest) status('Outline saved.');
    });
    if (course.job) node('p', `Generation: ${course.job.state} · ${course.lessons.length}/${course.blueprint.lessons.length} lessons · ${course.job.reserved_tokens}/${course.job.token_budget} tokens reserved${course.job.error ? ` · ${course.job.error}` : ''}`, panel);
    button('Refresh progress', panel, () => openCourse(id));
    if (!course.job || ['failed', 'cancelled'].includes(course.job.state)) button(course.job ? 'Resume generation' : 'Generate lessons', panel, async () => {
      await post(`/api/learning/courses/${id}/generation`, {action: course.job ? 'retry' : 'start',
        confirm_provider_use: $('course-provider-confirm').checked});
      if (requestId !== courseRequest) return;
      const refreshedRequest = courseRequest + 1; await openCourse(id);
      if (refreshedRequest === courseRequest) status('Generation queued. The configured worker will process one lesson at a time.');
    });
    if (course.job && ['queued', 'running'].includes(course.job.state)) button('Cancel generation', panel, async () => {
      await post(`/api/learning/courses/${id}/generation`, {action: 'cancel'});
      if (requestId === courseRequest) await openCourse(id);
    });
    for (const lesson of course.lessons) {
      const section = node('section', undefined, panel); section.className = 'learning-lesson';
      node('h4', course.blueprint.lessons.find(s => s.key === lesson.key).title, section);
      node('p', lesson.reviewed ? 'Human-reviewed course item · limited practice evidence' : 'Unreviewed generated material · check accuracy before using', section);
      for (const key of ['explanation', 'worked_example', 'practice', 'common_mistakes', 'review']) {
        if (lesson.content[key]) { node('h5', key.replaceAll('_', ' '), section); node('pre', lesson.content[key], section); }
      }
      for (const resource of lesson.content.resources || []) {
        const a = node('a', `${resource.provider}: ${resource.title}${resource.video ? ' · video on source page' : ''}`, section);
        a.href = resource.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        node('p', `Original source link only · metadata checked ${resource.checked_on}`, section);
      }
      if (lesson.attempt) {
        node('p', `${lesson.attempt.correct ? 'Recorded check passed.' : 'Recorded check needs review.'} ${lesson.attempt.explanation}`, section);
        node('p', 'For more practice, change the worked example and explain the result to the tutor. Repeating this item will not add independent evidence.', section);
      }
      if (lesson.reviewed && lesson.content.quiz && !lesson.attempt) {
        const quiz = lesson.content.quiz; node('h5', quiz.question, section);
        quiz.options.forEach((option, i) => button(option, section, async () => {
          const data = await post(`/api/learning/courses/${id}/lessons/${lesson.id}/answer`, {answer: i});
          if (requestId !== courseRequest) return;
          status(`${data.correct ? 'Correct.' : 'Review this idea.'} ${data.explanation}`); await openCourse(id);
        }));
      }
      if (!lesson.reviewed && review) {
        const quiz = review.lessons.find(l => l.id === lesson.id)?.content.quiz;
        if (quiz) {
          node('h5', `Answer-key review: ${quiz.question}`, section);
          quiz.options.forEach((option, i) => node('p', `${i === quiz.answer ? 'Expected answer: ' : ''}${option}`, section));
          node('p', quiz.explanation, section);
        }
      }
    }
    if (course.lessons.length === course.blueprint.lessons.length && course.state !== 'reviewed') {
      node('p', 'Review the source accuracy and all generated answer keys before confirming. Scoring remains locked until review.', panel);
      button('I have checked accuracy and every answer key', panel, async () => {
        await post(`/api/learning/courses/${id}/review`, {confirm_accuracy: true});
        if (requestId === courseRequest) await openCourse(id);
      });
    }
  }
  api('/api/learning/capabilities').then(data => {
    for (const [activity, key] of [['tutor','tutor'], ['study','study'], ['review','review'], ['courses','course_generator']]) {
      const b = document.querySelector(`[data-activity="${activity}"]`); b.hidden = !data.features[key].allowed;
      if (!data.features[key].allowed && activity === 'tutor') {
        document.querySelector('[data-panel="tutor"]').hidden = true;
        status(data.features[key].reason === 'institution_policy' ? 'Your institution has restricted this activity. Other permitted activities remain available.' : 'This activity is currently unavailable. Sign in to use private learning activities.');
      }
    }
    $('focus-link').hidden = !data.features.focus.allowed;
    if (data.features.study.allowed) restoreSitting().catch(() => {});
    api('/api/school-admin/organizations').then(result => { $('school-admin-link').hidden = !result.organizations.length; }).catch(() => {});
  }).catch(error => status(error.message));
})();
