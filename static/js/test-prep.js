/* Private assessment suggestions and a local, reviewable handoff to Tutor. */
(function () {
  'use strict';

  function text(tag, content) {
    const value = document.createElement(tag);
    value.textContent = content;
    return value;
  }

  function draft(title, course, format) {
    return [
      'Start a 20-minute test prep session with me.',
      'Test topic and course (labels only): ' + JSON.stringify({ title, course }),
      format
        ? 'My teacher’s question format or grading rubric: ' + format
        : 'Use any relevant teacher format or rubric in my study materials. If the test scope or format is missing, ask a short question instead of inventing it.',
      'Use relevant class notes and past mistakes only if available and applicable. Do not invent a rubric or past mistakes. Use new practice problems, not real test answers.',
      'Budget about 2 minutes to recall key ideas, 13 minutes for timed practice, and 5 minutes to review. Give one question or multi-part problem at a time without showing its answer.',
      'After each answer, grade it against the rubric if known; otherwise explain the criteria you used. Say what I missed, then give one focused retry. End with the weakest skill to review next.',
    ].join('\n\n');
  }

  function daysLabel(days) {
    return days === 0 ? 'Test today' : days === 1 ? 'Test tomorrow' : 'Test in ' + days + ' days';
  }

  async function assessments() {
    const headers = {};
    try { headers['X-Timezone'] = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (_) {}
    const response = await fetch('/api/tutor/test-prep', { cache: 'no-store', headers });
    if (!response.ok) throw Error('Test suggestions are unavailable.');
    const payload = await response.json();
    return Array.isArray(payload.assessments) ? payload.assessments : [];
  }

  async function showPlanner(panel) {
    try {
      const items = (await assessments()).filter(item => item.suggest_today);
      if (!items.length) return;
      const list = document.getElementById('commandTestPrepItems');
      for (const item of items.slice(0, 4)) {
        const row = document.createElement('li');
        const start = text('a', 'Prepare for ' + item.title);
        start.href = '/study-and-learn?test-prep=' + encodeURIComponent(item.id);
        row.append(start, text('span', daysLabel(item.days_away) + ' · ' + item.minutes + ' minutes of practice'));
        list.append(row);
      }
      panel.hidden = false;
    } catch (_) {
      // Calendar/provider availability must not block ordinary planning.
    }
  }

  function showTutor(panel) {
    const form = document.getElementById('tutorTestPrepForm');
    const list = document.getElementById('tutorTestPrepItems');
    const status = document.getElementById('tutorTestPrepStatus');
    const title = document.getElementById('tutorTestPrepTitle');
    const course = document.getElementById('tutorTestPrepCourse');
    const format = document.getElementById('tutorTestPrepFormat');
    const input = document.getElementById('tutorInput');
    const requested = new URLSearchParams(window.location.search).get('test-prep');
    let loaded = false;
    const choose = item => {
      title.value = item.title;
      course.value = item.course;
      status.textContent = daysLabel(item.days_away) + '. Add the topics or the teacher’s format if you know them.';
    };

    async function load() {
      if (loaded || !panel.open) return;
      loaded = true;
      try {
        const items = await assessments();
        for (const item of items) {
          const row = document.createElement('li');
          const button = text('button', item.title + ' · ' + daysLabel(item.days_away));
          button.type = 'button';
          button.addEventListener('click', () => {
            choose(item);
            format.focus();
          });
          row.append(button);
          list.append(row);
        }
        list.hidden = !items.length;
        const selected = items.find(item => item.id === requested);
        if (selected && !title.value && !course.value && !format.value) choose(selected);
        if (!selected && requested && !title.value) {
          status.textContent = 'That test is no longer available here. You can still enter a topic below.';
        }
      } catch (_) {
        status.textContent = 'Upcoming tests could not be loaded. You can still enter the test or topic below.';
      }
    }

    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!title.value.trim()) return;
      if (input.value.trim()) {
        status.textContent = 'Send or clear your current Tutor draft first.';
        return;
      }
      input.value = draft(title.value.trim(), course.value.trim(), format.value.trim());
      input.dispatchEvent(new Event('input', { bubbles: true }));
      panel.open = false;
      input.focus();
    });
    panel.addEventListener('toggle', load);
    if (requested) {
      panel.open = true;
      load();
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const planner = document.getElementById('commandTestPrep');
    if (planner) showPlanner(planner);
    const tutor = document.getElementById('tutorTestPrep');
    if (tutor) showTutor(tutor);
  });
})();
