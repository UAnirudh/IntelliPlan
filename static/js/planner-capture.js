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
      try {
        const draft = JSON.parse(sessionStorage.getItem('ip_photo_planner_draft') || 'null');
        sessionStorage.removeItem('ip_photo_planner_draft');
        if (draft && owner && draft.owner === owner && Date.now() - draft.at < 120000 &&
            typeof draft.text === 'string' && draft.text.length <= 12000) {
          text.value = draft.text;
          byId('plannerCapture').open = true;
          status.textContent = 'Keep one task per line, then select Review tasks.';
        }
      } catch (_) { /* The paste field remains available. */ }
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
    const now = new Date();
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
    day.value = local.toISOString().slice(0, 10);
    function read() {
      const stored = JSON.parse(localStorage.getItem(key) || '{}');
      return stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {};
    }
    function load() {
      try {
        const entry = read()[day.value] || {};
        note.value = entry.note || '';
        progress.value = entry.progress || '';
        status.textContent = '';
      } catch (_) {
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
        load();
        status.textContent = clear ? 'Cleared this day.' : 'Saved in this browser.';
      } catch (_) {
        status.textContent = 'Could not save in this browser.';
      }
    }
    day.addEventListener('change', load);
    checkin.addEventListener('submit', event => {
      event.preventDefault();
      write(false);
    });
    byId('plannerCheckinClear').addEventListener('click', () => write(true));
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
