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
    control.setAttribute('aria-busy', 'true');
    try { await action(); } catch (error) { status(error.message); }
    finally { control.removeAttribute('aria-busy'); control.disabled = control.dataset.finished === 'true' || control.dataset.locked === 'true'; }
  }
  const messages = []; let conversationId, sittingId, started, timer, courseRequest = 0, displayedCourseRequest = 0;
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
  function selectActivity(b) {
    document.querySelectorAll('[data-activity]').forEach(tab => tab.setAttribute('aria-pressed', String(tab === b)));
    document.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== b.dataset.activity; });
    if (b.dataset.activity === 'progress') loadProgress().catch(e => status(e.message));
    if (b.dataset.activity === 'courses') loadLibrary().catch(e => status(e.message));
  }
  document.querySelectorAll('[data-activity]').forEach(b => b.addEventListener('click', () => selectActivity(b)));
  document.querySelectorAll('[data-question]').forEach(b => b.addEventListener('click', () => {
    $('hub-question').value = b.dataset.question;
    $('tutor-activity').value = b.dataset.mode || 'learn';
    $('hub-question').focus();
  }));
  function addMessage(role, content) {
    const entry = node('article', undefined, $('hub-conversation'));
    entry.className = 'learning-message'; entry.dataset.role = role;
    node('h3', role === 'user' ? 'Your question' : 'IntelliPlan tutor', entry);
    node('p', content, entry);
    return entry;
  }
  $('hub-tutor-form').addEventListener('submit', event => {
    event.preventDefault();
    run(event.submitter, async () => {
      const question = $('hub-question').value.trim(); if (!question) return;
      const activity = $('tutor-activity').value;
      const message = {role: 'user', content: `${activity === 'practice' ? 'Guide me with hints and ask me to reason. ' : activity === 'review' ? 'Help me retrieve and review this concept. ' : 'Explain clearly, then check my understanding. '}${question}`};
      const entry = addMessage('user', question);
      let data;
      try { data = await post('/api/tutor', {messages: [...messages, message].slice(-16), conversation_id: conversationId}); }
      catch (error) { entry.remove(); throw error; }
      messages.push(message);
      messages.push({role: 'assistant', content: data.reply}); conversationId = data.conversation_id;
      addMessage('assistant', data.reply);
      if ($('hub-question').value.trim() === question) $('hub-question').value = '';
      $('tutor-starters').hidden = true;
      status(data.assessment_protection ? 'Your school’s assessment protection is active.' : '');
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
  let previewNoticeVersion, previewRequest = 0, previewBusy = false, previewAllowed = false, profileRequest = 0;
  const profileForm = node('form'); profileForm.id = 'practice-preview-profile';
  const profileLabel = node('label', 'Grade for reviewed practice', profileForm);
  const gradeSelect = node('select', undefined, profileLabel); gradeSelect.required = true; gradeSelect.id = 'practice-preview-grade';
  const grades = ['', 'Kindergarten', ...Array.from({length: 12}, (_, i) => `Grade ${i + 1}`), 'College'];
  for (const grade of grades) { const option = node('option', grade || 'Choose your grade', gradeSelect); option.value = grade; }
  const saveGrade = node('button', 'Save practice grade', profileForm); saveGrade.type = 'submit';
  function setPreviewBusy(value) {
    previewBusy = value;
    for (const control of [saveGrade, gradeSelect, $('practice-preview-next')]) {
      const locked = value || !previewAllowed;
      control.dataset.locked = String(locked); control.disabled = locked;
    }
  }
  gradeSelect.addEventListener('change', () => { ++profileRequest; });
  $('practice-preview-controls').prepend(profileForm);
  profileForm.addEventListener('submit', event => {
    event.preventDefault(); run(event.submitter, async () => {
      if (previewBusy) return;
      ++profileRequest; setPreviewBusy(true);
      try {
        await post('/api/learning/practice-preview/profile', {grade_level: gradeSelect.value});
        ++previewRequest; $('practice-preview-check').replaceChildren();
        status('Practice grade saved. Start a fresh reviewed check.');
      } finally { setPreviewBusy(false); }
    });
  });
  async function loadPreview() {
    const state = await api('/api/learning/beta/practice_preview');
    $('practice-preview').hidden = !state.approved;
    previewNoticeVersion = state.notice_version;
    $('practice-preview-notice').textContent = state.notice + (state.allowed ? '' : ` Availability: ${state.reason.replaceAll('_', ' ')}.`);
    $('practice-preview-consent').hidden = state.accepted || state.reason !== 'beta_consent_required';
    $('practice-preview-controls').hidden = !state.accepted;
    previewAllowed = state.allowed; setPreviewBusy(previewBusy);
    const profileId = ++profileRequest;
    if (state.allowed) api('/api/learning/practice-preview/profile').then(data => {
      if (profileId === profileRequest && grades.includes(data.profile.grade_level)) gradeSelect.value = data.profile.grade_level;
    }).catch(error => status(error.message));
  }
  $('practice-preview-consent').addEventListener('submit', event => {
    event.preventDefault(); run(event.submitter, async () => {
      await post('/api/learning/beta/practice_preview', {accept: true, notice_version: previewNoticeVersion});
      await loadPreview(); status('You joined the optional practice preview. You can leave at any time.');
    });
  });
  $('practice-preview-withdraw').addEventListener('click', event => run(event.currentTarget, async () => {
    await post('/api/learning/beta/practice_preview', {accept: false});
    ++previewRequest;
    $('practice-preview-check').replaceChildren(); await loadPreview(); status('You left the preview. Existing practice evidence is preserved.');
  }));
  $('practice-preview-next').addEventListener('click', event => run(event.currentTarget, async () => {
    if (previewBusy) return;
    const requestId = ++previewRequest;
    $('practice-preview-check').replaceChildren(); status('Preparing a reviewed check…');
    let challenge; setPreviewBusy(true);
    try { challenge = await post('/api/learning/practice-preview/check', {area: $('practice-preview-area').value}); }
    finally { setPreviewBusy(false); }
    if (requestId !== previewRequest) return;
    const panel = $('practice-preview-check'); panel.replaceChildren();
    node('h4', challenge.skill.title, panel); node('p', challenge.reason, panel); node('p', challenge.item.prompt, panel);
    const controls = [];
    let scoring = false, finished = false;
    async function score(action) {
      if (previewBusy || requestId !== previewRequest || scoring || finished) return;
      scoring = true; setPreviewBusy(true);
      for (const control of controls) control.disabled = true;
      try { await action(); } finally {
        scoring = false;
        setPreviewBusy(false);
        if (requestId === previewRequest) {
          for (const control of controls) { control.disabled = finished; control.dataset.finished = String(finished); }
        }
      }
    }
    const submitAnswer = answer => score(async () => {
      const result = await post('/api/learning/practice-preview/answer', {token: challenge.token, answer});
      if (requestId !== previewRequest) return;
      finished = true;
      const feedback = `${result.correct ? 'Check passed.' : 'Try a different example after reviewing.'} ${result.feedback || ''}`;
      node('p', feedback, panel);
      status(`${feedback} Saved by the existing reviewed practice service. One result does not establish mastery.`);
    });
    if (challenge.item.options.length) {
      for (const option of challenge.item.options) controls.push(button(option, panel, () => submitAnswer(option)));
    } else {
      const form = node('form', undefined, panel); const label = node('label', 'Your answer', form);
      const input = node('input', undefined, label); input.required = true; input.maxLength = 200;
      const submit = node('button', 'Check my answer', form); submit.type = 'submit'; controls.push(input, submit);
      form.addEventListener('submit', event => { event.preventDefault(); run(submit, () => submitAnswer(input.value)); });
    }
    controls.push(button('Show a hint', panel, () => score(async () => {
      const result = await post('/api/learning/practice-preview/hint', {token: challenge.token});
      if (requestId !== previewRequest) return;
      node('p', `Hint: ${result.hint}. This attempt will be recorded as assisted.`, panel);
      status(`Hint: ${result.hint}. This attempt will be recorded as assisted.`);
    })));
  }));
  $('practice-preview-feedback').addEventListener('submit', event => {
    event.preventDefault(); run(event.submitter, async () => {
      await post('/api/learning/beta/practice_preview/feedback', {category:'feedback', message:$('practice-preview-message').value});
      $('practice-preview-message').value = ''; status('Feedback saved for your institution administrators.');
    });
  });
  $('hub-course-form').addEventListener('submit', event => {
    event.preventDefault(); run(event.submitter, async () => {
      const data = await post('/api/learning/courses', {goal: $('course-goal').value, mode: $('course-mode').value,
        confirm_provider_use: $('course-provider-confirm').checked});
      await openCourse(data.id); await loadLibrary(); status('Outline saved. Review the sequence before starting generation.');
    });
  });
  async function loadLibrary() {
    const data = await api('/api/learning/courses'); const panel = $('course-library'); panel.replaceChildren();
    node('h3', 'Your saved courses', panel);
    if (!data.courses.length) node('p', 'Start with a learning goal above. Your outline and lessons will stay here when you return.', panel).className = 'learning-empty';
    for (const course of data.courses) {
      const entry = button(`${course.title}${course.revision_source ? ` · revision ${course.revision}` : ''} · ${course.state}`, panel, () => openCourse(course.id));
      entry.dataset.courseId = String(course.id);
    }
  }
  async function openCourse(id) {
    const requestId = ++courseRequest;
    const panel = $('course-workspace');
    panel.inert = true; panel.setAttribute('aria-busy', 'true');
    let course, review;
    try {
      course = await api(`/api/learning/courses/${id}`);
      if (requestId !== courseRequest) return;
      review = course.state !== 'reviewed' ? await api(`/api/learning/courses/${id}/review`) : null;
    } catch (error) {
      if (requestId === courseRequest) {
        panel.inert = false;
        if (!panel.dataset.mutationRequest) panel.removeAttribute('aria-busy');
        throw error;
      }
      return; // A newer course selection owns the workspace and its status.
    }
    if (requestId !== courseRequest) return;
    displayedCourseRequest = requestId;
    panel.inert = false; panel.dataset.courseId = String(id); panel.replaceChildren();
    delete panel.dataset.mutationRequest;
    panel.removeAttribute('aria-busy');
    let workspaceBusy = false;
    const isCurrentCourse = () => requestId === displayedCourseRequest && !panel.inert;
    async function mutateCourse(action) {
      if (workspaceBusy || !isCurrentCourse()) return;
      workspaceBusy = true;
      const controls = [...panel.querySelectorAll('button, input, textarea, select')];
      const disabled = controls.map(control => control.disabled);
      controls.forEach(control => { control.disabled = true; });
      panel.setAttribute('aria-busy', 'true');
      panel.dataset.mutationRequest = String(requestId);
      try { await action(); }
      catch (error) {
        if (isCurrentCourse()) throw error;
        // A response for a replaced course must not overwrite the visible status.
      }
      finally {
        controls.forEach((control, index) => { control.disabled = disabled[index]; });
        workspaceBusy = false;
        if (panel.dataset.mutationRequest === String(requestId)) delete panel.dataset.mutationRequest;
        if (isCurrentCourse()) panel.removeAttribute('aria-busy');
      }
    }
    async function showRevision(result) {
      if (!isCurrentCourse()) return;
      const displayRequest = courseRequest + 1;
      await openCourse(result.id); await loadLibrary();
      if (displayRequest === displayedCourseRequest && !panel.inert) status(result.message);
    }
    node('h3', course.blueprint.title, panel); node('p', course.blueprint.overview, panel);
    if (course.blueprint.revision_source) {
      node('p', `Revision ${course.revision}: the original course and its evidence are preserved. Copied checks do not become independent evidence.`, panel);
      button('Open original course', panel, () => openCourse(course.blueprint.revision_source.course_id));
    }
    const stages = node('ol', undefined, panel); stages.className = 'learning-course-stages'; stages.setAttribute('aria-label', 'Course preparation');
    const stage = course.state === 'reviewed' ? 2 : course.lessons.length === course.blueprint.lessons.length ? 2 : course.job ? 1 : 0;
    ['Outline', 'Lessons', 'Human review'].forEach((label, i) => {
      const item = node('li', label, stages);
      if (i === stage) item.setAttribute('aria-current', 'step');
    });
    node('p', `Next: ${course.next.key || 'Review'} · ${course.next.reason}`, panel);
    const outline = node('details', undefined, panel); outline.className = 'learning-outline'; outline.open = course.state === 'draft';
    node('summary', `Course outline · ${course.blueprint.lessons.length} lessons`, outline);
    const profileText = [['subject', 'Subject'], ['current_level', 'Current level'], ['desired_depth', 'Desired depth'],
      ['available_time', 'Available time'], ['target_period', 'Target completion period']];
    const profileLists = [['learning_goals', 'Learning goals'], ['preferred_methods', 'Preferred teaching methods'],
      ['prerequisites', 'Relevant prerequisites'], ['desired_assessments', 'Desired assessments'],
      ['project_preferences', 'Project preferences'], ['accessibility_preferences', 'Voluntary accessibility preferences']];
    const preferences = node('fieldset', undefined, outline); node('legend', 'Course request details', preferences);
    node('p', 'These are request preferences, not verified knowledge or mastery. Review inferred details before generation. Original lessons use the saved details; no stored account or student records are automatically attached.', preferences);
    if (!course.blueprint.request_profile) node('p', 'Request details were not extracted. Your original goal is still saved.', preferences);
    const profileFields = [];
    const profile = course.blueprint.request_profile || {};
    for (const [key, title] of [...profileText, ...profileLists]) {
      const isList = profileLists.some(([list]) => key === list);
      const value = isList ? (profile[key] || []).join('\n') : profile[key] || 'Not specified';
      if (course.state !== 'draft') { if (value) node('p', `${title}: ${isList ? (profile[key] || []).join('; ') : value}`, preferences); continue; }
      const label = node('label', `${title}${isList ? ' (one per line, up to six)' : ''}`, preferences);
      const input = node(isList ? 'textarea' : 'input', undefined, label);
      input.name = `profile_${key}`; input.value = value; input.maxLength = isList ? 1206 : 200;
      if (!isList) input.required = true;
      profileFields.push([key, input, isList]);
    }
    node('p', 'Requested assessments, projects and teaching methods do not imply they are supported or validated. The current graded course item is a reviewed multiple-choice check.', preferences).className = 'learning-note';
    if (course.mode === 'curated') node('p', 'Curated beta uses a fixed reviewed foundations catalog. These details are saved for your planning; they do not change source selection or difficulty.', preferences).className = 'learning-note';
    const fields = [];
    for (const spec of course.blueprint.lessons) {
      if (course.state !== 'draft') { node('p', `${spec.title} · ${spec.unit} · ${spec.minutes} minutes`, outline); continue; }
      const label = node('label', `${spec.unit} · ${spec.minutes} minutes`, outline);
      const input = node('input', undefined, label); input.value = spec.title; input.maxLength = 200;
      input.disabled = course.state !== 'draft'; fields.push([spec, input]);
    }
    if (course.state === 'draft') button('Save outline edits', outline, () => mutateCourse(async () => {
      for (const [spec, input] of fields) spec.title = input.value;
      course.blueprint.request_profile = Object.fromEntries(profileFields.map(([key, input, isList]) =>
        [key, isList ? input.value.split('\n').map(value => value.trim()).filter(Boolean) : input.value]));
      const saved = await api(`/api/learning/courses/${id}`, {method: 'PUT', body: JSON.stringify({blueprint: course.blueprint, revision:course.revision})});
      course.revision = saved.revision;
      if (isCurrentCourse()) status('Outline saved.');
    }));
    if (course.lessons.length === course.blueprint.lessons.length && !['queued', 'running'].includes(course.job?.state)) {
      const organization = node('details', undefined, panel); organization.className = 'learning-outline learning-course-organization';
      node('summary', 'Revise course organization', organization);
      node('p', 'Change grouping, titles, duration or lesson order in a new course revision. Keep the same objectives and teaching content. Prerequisites must come before the lessons that use them; check the revised sequence before review.', organization);
      const editor = node('div', undefined, organization);
      button('Edit units and lesson order', organization, () => mutateCourse(async () => {
        if (editor.children.length) return;
        const form = node('form', undefined, editor); form.className = 'learning-organization-edit';
        function field(labelText, value, parent, maximum = 200, multiline = false) {
          const label = node('label', labelText, parent);
          const input = node(multiline ? 'textarea' : 'input', undefined, label);
          input.value = value; input.maxLength = maximum; input.required = true; return input;
        }
        const title = field('Course title', course.blueprint.title, form);
        const overview = field('Course overview', course.blueprint.overview, form, 2000, true);
        const lessons = node('div', undefined, form);
        const order = []; const moves = [];
        function refreshMoves() {
          moves.forEach(({spec, earlier, later}) => {
            const index = order.indexOf(spec);
            earlier.disabled = index === 0; earlier.dataset.locked = String(earlier.disabled);
            later.disabled = index === order.length - 1; later.dataset.locked = String(later.disabled);
          });
        }
        for (const spec of course.blueprint.lessons) {
          order.push(spec);
          const card = node('fieldset', undefined, lessons); node('legend', spec.title, card);
          const lessonTitle = field('Lesson title', spec.title, card);
          const unit = field('Unit', spec.unit, card);
          const minutes = field('Planned minutes', spec.minutes, card);
          minutes.type = 'number'; minutes.min = '5'; minutes.max = '120'; minutes.step = '1';
          const prerequisites = node('details', undefined, card);
          node('summary', 'Prerequisite lessons', prerequisites);
          const choices = [];
          for (const candidate of course.blueprint.lessons.filter(item => item.key !== spec.key)) {
            const label = node('label', undefined, prerequisites);
            label.className = 'learning-consent';
            const check = node('input', undefined, label); check.type = 'checkbox';
            check.value = candidate.key; check.checked = spec.prerequisites.includes(candidate.key);
            label.append(document.createTextNode(candidate.title)); choices.push(check);
          }
          const earlier = button(`${spec.title}: Move earlier`, card, () => {
            const index = order.indexOf(spec); if (index === 0) return;
            order.splice(index, 1); order.splice(index - 1, 0, spec);
            lessons.insertBefore(card, lessons.children[index - 1]); refreshMoves();
          });
          const later = button(`${spec.title}: Move later`, card, () => {
            const index = order.indexOf(spec); if (index === order.length - 1) return;
            order.splice(index, 1); order.splice(index + 1, 0, spec);
            lessons.insertBefore(lessons.children[index + 1], card); refreshMoves();
          });
          moves.push({spec, earlier, later, lessonTitle, unit, minutes, choices});
        }
        refreshMoves();
        const save = node('button', 'Save course organization in a revised course', form); save.type = 'submit';
        form.addEventListener('submit', event => {
          event.preventDefault(); run(save, () => mutateCourse(async () => {
            const blueprint = structuredClone(course.blueprint);
            blueprint.title = title.value; blueprint.overview = overview.value;
            blueprint.lessons = order.map(spec => {
              const fields = moves.find(item => item.spec === spec);
              return {...spec, title:fields.lessonTitle.value, unit:fields.unit.value, minutes:Number(fields.minutes.value),
                prerequisites:fields.choices.filter(check => check.checked).map(check => check.value)};
            });
            const result = await post(`/api/learning/courses/${id}/outline-revision`, {blueprint, revision:course.revision});
            await showRevision(result);
          }));
        });
        title.focus();
      }));
    }
    if (course.job) node('p', `Generation: ${course.job.state} · ${course.lessons.length}/${course.blueprint.lessons.length} lessons · ${course.job.reserved_tokens}/${course.job.token_budget} tokens reserved${course.job.error ? ` · ${course.job.error}` : ''}`, panel);
    if (course.job) {
      const saved = node('progress', undefined, panel); saved.max = course.blueprint.lessons.length; saved.value = course.lessons.length;
      saved.setAttribute('aria-label', 'Lessons saved');
    }
    button('Refresh progress', panel, () => openCourse(id));
    if (course.lessons.length < course.blueprint.lessons.length && (!course.job || ['failed', 'cancelled'].includes(course.job.state))) button(course.job ? 'Resume generation' : 'Generate lessons', panel, () => mutateCourse(async () => {
      await post(`/api/learning/courses/${id}/generation`, {action: course.job ? 'retry' : 'start',
        confirm_provider_use: $('course-provider-confirm').checked});
      if (!isCurrentCourse()) return;
      const refreshedRequest = courseRequest + 1; await openCourse(id);
      if (refreshedRequest === displayedCourseRequest && !panel.inert) status('Generation queued. The configured worker will process one lesson at a time.');
    }));
    if (course.job && ['queued', 'running'].includes(course.job.state)) button('Cancel generation', panel, () => mutateCourse(async () => {
      await post(`/api/learning/courses/${id}/generation`, {action: 'cancel'});
      if (isCurrentCourse()) await openCourse(id);
    }));
    for (const lesson of course.lessons) {
      const section = node('section', undefined, panel); section.className = 'learning-lesson';
      node('h4', course.blueprint.lessons.find(s => s.key === lesson.key).title, section);
      node('p', lesson.reviewed ? 'Human-reviewed course item · limited practice evidence' :
        (course.mode === 'curated' ? 'Unreviewed curated-path activity · check sources and original practice before using' :
          'Unreviewed generated material · check accuracy before using'), section);
      if (lesson.content.assessment_origin === 'fixed_original_v1') {
        node('p', 'Fixed original concept check · English · course-builder results are assisted evaluation evidence', section);
      }
      if (lesson.content.assessment_notice) node('p', lesson.content.assessment_notice, section);
      for (const key of ['explanation', 'worked_example', 'practice', 'common_mistakes', 'review']) {
        if (lesson.content[key]) {
          node('h5', key.replaceAll('_', ' '), section);
          const content = node('div', undefined, section);
          if (!window.LearningContent?.render(content, lesson.content[key])) {
            content.classList.add('learning-content'); content.dataset.rendering = 'plain-text';
            content.textContent = lesson.content[key];
            node('p', 'Formatted content is unavailable. The lesson is shown as plain text.', section).className = 'learning-note';
          }
        }
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
        quiz.options.forEach((option, i) => button(option, section, () => mutateCourse(async () => {
          const data = await post(`/api/learning/courses/${id}/lessons/${lesson.id}/answer`, {answer: i});
          if (!isCurrentCourse()) return;
          status(`${data.correct ? 'Correct.' : 'Review this idea.'} ${data.explanation}`); await openCourse(id);
        })));
      }
      if (!lesson.reviewed && review) {
        const quiz = review.lessons.find(l => l.id === lesson.id)?.content.quiz;
        if (quiz) {
          node('h5', `Answer-key review: ${quiz.question}`, section);
          quiz.options.forEach((option, i) => node('p', `${i === quiz.answer ? 'Expected answer: ' : ''}${option}`, section));
          node('p', quiz.explanation, section);
        }
      }
      if (course.mode === 'generated' && course.lessons.length === course.blueprint.lessons.length &&
          !['queued', 'running'].includes(course.job?.state)) {
        const revision = node('details', undefined, section); revision.className = 'learning-outline learning-lesson-revision';
        node('summary', 'Revise this lesson', revision);
        node('p', 'Save a revised course with this lesson changed. Your original course and recorded checks stay available. The revised course needs a new accuracy review.', revision);
        node('p', 'A replacement knowledge check keeps the teaching sections and requests a different example. Check its accuracy and alignment before scoring. Course-builder results remain assisted review evidence.', revision);
        button('Create a replacement knowledge check', revision, () => mutateCourse(async () => {
          const result = await post(`/api/learning/courses/${id}/lessons/${lesson.id}/revision`, {
            action:'new_check', confirm_provider_use:$('course-provider-confirm').checked,
          });
          await showRevision(result);
        }));
        button('Regenerate this lesson in a revised course', revision, () => mutateCourse(async () => {
          const result = await post(`/api/learning/courses/${id}/lessons/${lesson.id}/revision`, {
            action:'regenerate', confirm_provider_use:$('course-provider-confirm').checked,
          });
          await showRevision(result);
        }));
        const editor = node('div', undefined, revision);
        button('Edit this lesson', revision, () => mutateCourse(async () => {
          if (editor.children.length) return;
          const keys = await api(`/api/learning/courses/${id}/review`);
          if (!isCurrentCourse() || !editor.isConnected) return;
          const original = keys.lessons.find(item => item.id === lesson.id).content;
          const form = node('form', undefined, editor); form.className = 'learning-lesson-edit';
          const fields = {};
          for (const key of ['explanation', 'worked_example', 'practice', 'common_mistakes', 'review']) {
            const label = node('label', key.replaceAll('_', ' '), form);
            const input = node('textarea', undefined, label); input.value = original[key];
            input.maxLength = 10000; input.required = true; input.name = key; fields[key] = input;
          }
          const check = node('fieldset', undefined, form); node('legend', 'Knowledge check and answer key', check);
          function checkField(labelText, value, name, maximum) {
            const label = node('label', labelText, check); const input = node('textarea', undefined, label);
            input.value = value; input.name = name; input.maxLength = maximum; input.required = true; return input;
          }
          const question = checkField('Question', original.quiz.question, 'question', 2000);
          const options = original.quiz.options.map((option, index) => checkField(`Option ${index + 1}`, option, `option_${index}`, 1000));
          const answerLabel = node('label', 'Correct option', check); const answer = node('select', undefined, answerLabel);
          answer.name = 'answer'; original.quiz.options.forEach((option, index) => {
            const choice = node('option', `Option ${index + 1}`, answer); choice.value = String(index);
          });
          answer.value = String(original.quiz.answer);
          const explanation = checkField('Answer explanation', original.quiz.explanation, 'answer_explanation', 3000);
          const save = node('button', 'Save lesson in a revised course', form); save.type = 'submit';
          form.addEventListener('submit', event => {
            event.preventDefault(); run(save, () => mutateCourse(async () => {
              const content = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value]));
              content.quiz = {question:question.value, options:options.map(option => option.value),
                answer:Number(answer.value), explanation:explanation.value};
              const result = await post(`/api/learning/courses/${id}/lessons/${lesson.id}/revision`, {action:'edit', content});
              await showRevision(result);
            }));
          });
          fields.explanation.focus();
        }));
        revision.append(editor);
      }
    }
    if (course.lessons.length === course.blueprint.lessons.length && course.state !== 'reviewed') {
      node('p', 'Review the source accuracy and all generated answer keys before confirming. Scoring remains locked until review.', panel);
      button('I have checked accuracy and every answer key', panel, () => mutateCourse(async () => {
        await post(`/api/learning/courses/${id}/review`, {confirm_accuracy: true});
        if (isCurrentCourse()) await openCourse(id);
      }));
    }
  }
  api('/api/learning/capabilities').then(data => {
    loadPreview().catch(() => { $('practice-preview').hidden = true; });
    for (const [activity, key] of [['tutor','tutor'], ['study','study'], ['review','review'], ['courses','course_generator'], ['progress','study']]) {
      const b = document.querySelector(`[data-activity="${activity}"]`); b.hidden = !data.features[key].allowed;
      if (!data.features[key].allowed && activity === 'tutor') {
        document.querySelector('[data-panel="tutor"]').hidden = true;
        status(data.features[key].reason === 'institution_policy' ? 'Your institution has restricted this activity. Other permitted activities remain available.' : 'This activity is currently unavailable. Sign in to use private learning activities.');
      }
    }
    const active = document.querySelector('[data-activity][aria-pressed="true"]');
    if (active.hidden) {
      const permitted = [...document.querySelectorAll('[data-activity]')].find(b => !b.hidden);
      if (permitted) selectActivity(permitted);
      else {
        document.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = true; });
        $('hub-unavailable').hidden = false;
        document.querySelector('.learning-guide').hidden = true;
        document.querySelector('.learning-activities').hidden = true;
        status('');
      }
    }
    $('focus-link').hidden = !data.features.focus.allowed;
    if (data.features.study.allowed) restoreSitting().catch(() => {});
    api('/api/school-admin/organizations').then(result => { $('school-admin-link').hidden = !result.organizations.length; }).catch(() => {});
  }).catch(error => status(error.message));
})();
