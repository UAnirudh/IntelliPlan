(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const owner = document.body.dataset.userId;
  const form = byId('plannerCaptureForm');
  if (form) {
    const text = byId('plannerCaptureText');
    const file = byId('plannerCaptureFile');
    const preview = byId('plannerCapturePreview');
    const status = byId('plannerCaptureStatus');
    const review = byId('plannerCaptureReview');
    const save = byId('plannerCaptureSave');
    let payload = null;
    let revision = 0;
    if (new URLSearchParams(location.search).get('capture') === '1') {
      byId('plannerCapture').open = true;
      const draft = window.IPPlannerDraft?.take();
      if (draft) {
        text.value = draft;
        status.textContent = 'Check the task and time estimate, then select Review tasks.';
      }
    }

    function clearReview() {
      revision += 1;
      payload = null;
      preview.replaceChildren();
      preview.hidden = true;
      save.hidden = true;
      status.textContent = '';
    }

    async function send(body) {
      const response = await fetch('/api/quick-add/batch', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || 'Could not read the task list.');
      return result;
    }

    text.addEventListener('input', clearReview);
    file.addEventListener('change', clearReview);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      clearReview();
      const requested = revision;
      const source = file.files[0];
      if (source && text.value.trim()) {
        status.textContent = 'Use either pasted tasks or a CSV. Clear the other field first.';
        return;
      }
      if (source && source.size > 96000) {
        status.textContent = 'Choose a smaller Todoist CSV.';
        return;
      }
      review.disabled = true;
      try {
        const draft = {
          format: source ? 'todoist' : 'lines',
          text: source ? await source.text() : text.value,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        };
        const result = await send(draft);
        if (requested !== revision) return;
        payload = {...draft, confirmation: result.confirmation};
        for (const task of result.tasks) {
          const item = document.createElement('li');
          item.textContent = [task.title, task.due_date || 'No due date', task.estimated_time + ' min'].join(' · ');
          preview.appendChild(item);
        }
        preview.hidden = false;
        save.hidden = false;
        status.textContent = result.warnings.join(' ');
      } catch (error) {
        if (requested === revision) status.textContent = error.message;
      } finally {
        review.disabled = false;
      }
    });
    save.addEventListener('click', async () => {
      if (!payload) return;
      for (const item of [text, file, review, save]) item.disabled = true;
      try {
        const result = await send(payload);
        text.value = '';
        file.value = '';
        clearReview();
        status.textContent = 'Added ' + result.added + ' tasks.' +
          (result.skipped ? ' Skipped ' + result.skipped + ' duplicates.' : '') +
          ' Build a study plan to place them.';
        if (typeof window.loadSchedulerAssignments === 'function') window.loadSchedulerAssignments();
      } catch (error) {
        status.textContent = error.message;
      } finally {
        for (const item of [text, file, review, save]) item.disabled = false;
      }
    });
  }

  const checkin = byId('plannerCheckinForm');
  if (checkin && owner) {
    const key = 'ip_planner_days:' + owner;
    const day = byId('plannerCheckinDay');
    const note = byId('plannerCheckinNote');
    const progress = byId('plannerCheckinProgress');
    const status = byId('plannerCheckinStatus');
    const adjustment = byId('plannerCheckinAdjust');
    const now = new Date();
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
    const agenda = byId('ivDays');
    const today = local.toISOString().slice(0, 10);
    const adjustments = {
      continue: ['catch_up', 'Preview a catch-up plan'],
      rest: ['skip_day', 'Preview taking the rest of today off'],
    };
    day.value = today;
    let savedAdjustment;
    function read() {
      const stored = JSON.parse(localStorage.getItem(key) || '{}');
      return stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {};
    }
    function renderDayNotes() {
      let entries;
      try { entries = read(); } catch (_) { return; }
      agenda.querySelectorAll('[data-planner-day-note]').forEach(card => {
        const text = entries[card.dataset.plannerDayNote]?.note;
        card.textContent = typeof text === 'string' ? text : '';
        card.hidden = !card.textContent;
      });
    }
    function load() {
      try {
        const entry = read()[day.value] || {};
        note.value = entry.note || '';
        progress.value = entry.progress || '';
        savedAdjustment = day.value === today && Object.hasOwn(adjustments, entry.progress)
          ? adjustments[entry.progress] : undefined;
        adjustment.hidden = !savedAdjustment;
        if (savedAdjustment) adjustment.textContent = savedAdjustment[1];
        status.textContent = '';
      } catch (_) {
        adjustment.hidden = true;
        status.textContent = 'Browser storage is unavailable.';
      }
    }
    function write(clear) {
      if (!day.value) return;
      try {
        const entries = read();
        if (clear || (!note.value.trim() && !progress.value)) delete entries[day.value];
        else entries[day.value] = {note: note.value.trim(), progress: progress.value};
        const recent = Object.keys(entries).sort().slice(-30);
        localStorage.setItem(key, JSON.stringify(Object.fromEntries(recent.map(date => [date, entries[date]]))));
        renderDayNotes();
        load();
        status.textContent = clear ? 'Cleared this day.' : 'Saved in this browser.';
      } catch (_) {
        status.textContent = 'Could not save in this browser.';
      }
    }
    day.addEventListener('change', load);
    progress.addEventListener('change', () => { adjustment.hidden = true; });
    adjustment.addEventListener('click', () => {
      if (!savedAdjustment || adjustment.hidden) return;
      if (!window.FT?.available()) {
        status.textContent = 'Build and save a plan before previewing an adjustment.';
        return;
      }
      window.FT.open(savedAdjustment[0], {}, adjustment);
    });
    checkin.addEventListener('submit', event => {
      event.preventDefault();
      write(false);
    });
    byId('plannerCheckinClear').addEventListener('click', () => write(true));
    agenda.addEventListener('planner-days-rendered', renderDayNotes);
    renderDayNotes();
    load();
  }

  const focus = byId('ckFocus');
  if (focus) focus.addEventListener('click', event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    try {
      sessionStorage.setItem('ip_planned_focus', JSON.stringify({
        owner: owner || '', title: focus.dataset.title, minutes: Number(focus.dataset.minutes), at: Date.now(),
      }));
    } catch (_) { /* The ordinary timer also works without browser storage. */ }
  });
})();

(() => {
  const panel = document.getElementById('plannerTodoist');
  if (!panel) return;
  const form = document.getElementById('plannerTodoistProjects');
  const choices = document.getElementById('plannerTodoistChoices');
  const connect = document.getElementById('plannerTodoistConnect');
  const disconnect = document.getElementById('plannerTodoistDisconnect');
  const status = document.getElementById('plannerTodoistStatus');
  let busy = false;
  async function communicate(method, projects) {
    if (busy) return;
    busy = true;
    choices.disabled = true;
    status.textContent = 'Checking Todoist…';
    try {
      const response = await fetch('/api/todoist/connection', {method, credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        ...(projects ? {body: JSON.stringify({projects})} : {})});
      const state = await response.json();
      disconnect.hidden = !state.connected;
      if (!response.ok) throw new Error(state.message || 'Todoist could not be reached.');
      form.hidden = !state.connected;
      connect.hidden = state.connected || state.available === false;
      choices.querySelectorAll('label').forEach(label => label.remove());
      for (const project of state.projects || []) {
        const label = document.createElement('label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = project.id;
        checkbox.checked = state.selected.includes(project.id);
        label.append(checkbox, ' ' + project.name);
        choices.append(label);
      }
      status.textContent = state.available === false ? 'Todoist sync is unavailable for this workspace.'
        : method === 'POST' ? 'Projects saved. Your planner will read their current tasks.'
          : method === 'DELETE' ? 'Todoist disconnected.' : '';
      if (method !== 'GET' && typeof window.loadSchedulerAssignments === 'function') window.loadSchedulerAssignments();
    } catch (error) {
      status.textContent = error.message;
      connect.hidden = false;
    } finally {
      choices.disabled = false;
      busy = false;
    }
  }
  panel.addEventListener('toggle', () => { if (panel.open) communicate('GET'); });
  form.addEventListener('submit', event => {
    event.preventDefault();
    communicate('POST', Array.from(choices.querySelectorAll('input:checked'), input => input.value));
  });
  disconnect.addEventListener('click', () => communicate('DELETE'));
  const oauth = new URLSearchParams(location.search).get('todoist');
  if (oauth) {
    document.getElementById('plannerCapture').open = true;
    panel.open = true;
    if (oauth === 'failed') status.textContent = 'Todoist could not connect. Try again.';
  }
})();
