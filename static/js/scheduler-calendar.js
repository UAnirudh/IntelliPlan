(function () {
  'use strict';

  const tab = document.getElementById('tab-calendar');
  if (!tab) return;

  const params = new URLSearchParams(location.search);
  const date = document.getElementById('agendaDate');
  if (!date) {
    if (params.get('view') === 'calendar') tab.click();
    return;
  }

  const heading = document.getElementById('agendaDayHeading');
  const status = document.getElementById('agendaStatus');
  const items = document.getElementById('agendaItems');
  const exams = document.getElementById('agendaExams');
  const range = document.getElementById('agendaRange');
  const viewButtons = Array.from(document.querySelectorAll('[data-agenda-view]'));
  let view = 'day';
  const form = document.getElementById('agendaActivity');
  const activityStatus = document.getElementById('agendaActivityStatus');
  const find = document.getElementById('agendaFind');
  function dateValue(day) {
    return [day.getFullYear(), day.getMonth() + 1, day.getDate()]
      .map(part => String(part).padStart(2, '0')).join('-');
  }
  const today = () => dateValue(new Date());
  const dateTitle = new Intl.DateTimeFormat(undefined, {
    weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
  });
  const monthTitle = new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' });
  const shortDay = new Intl.DateTimeFormat(undefined, { weekday: 'short' });
  const shortDate = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
  const clock = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const labels = { class: 'Class', study: 'Study', break: 'Break', busy: 'Calendar', commitment: 'Activity' };

  let dayRequest;
  let slotRequest;
  date.value = params.get('date') || today();
  if (!date.value) date.value = today();

  async function readDay(day, signal, duration, days = 1) {
    const query = new URLSearchParams({ date: day, tz: timezone });
    if (duration) query.set('duration', duration);
    if (days > 1) query.set('days', days);
    const response = await fetch('/api/scheduler/calendar?' + query, { signal, cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || 'Could not load your calendar.');
    return data;
  }

  function timeLabel(day, time) {
    if (time === '24:00') return clock.format(new Date(day + 'T00:00'));
    return clock.format(new Date(day + 'T' + time));
  }

  function renderItem(item, day) {
    const row = document.createElement('li');
    row.className = 'agenda-item';
    const time = document.createElement('time');
    time.dateTime = day + 'T' + item.start;
    time.textContent = item.label === 'All day' ? 'All day' :
      timeLabel(day, item.start) + ' to ' + timeLabel(day, item.end);
    const copy = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = item.title;
    const detail = document.createElement('small');
    detail.textContent = [
      labels[item.kind], item.subtitle, item.done ? 'Done' : '',
    ].filter(Boolean).join(' · ');
    copy.append(title, detail);
    row.append(time, copy);
    if (item.kind === 'study' && !item.done) {
      const action = document.createElement('a');
      action.className = 'agenda-item-start';
      action.href = '/active?' + new URLSearchParams({ block: item.id, date: day });
      action.textContent = 'Start study';
      action.setAttribute('aria-label', 'Start study for ' + item.title);
      row.appendChild(action);
    }
    return row;
  }

  function renderExams(rows, day) {
    exams.replaceChildren(...rows.map(exam => {
      const row = document.createElement('li');
      const title = document.createElement('strong');
      title.textContent = exam.title + ' is tomorrow. ';
      const message = exam.review ?
        'Review ' + (exam.review[0] === day ? 'today' : 'tomorrow') + ' at ' +
          timeLabel(exam.review[0], exam.review[1]) + '.' :
        'There’s no review time on your plan yet.';
      row.append(title, message);
      if (exam.unscheduled && exam.review) row.append(' Some prep still needs time.');
      return row;
    }));
    exams.hidden = !rows.length;
  }

  function selectView(next) {
    view = next;
    viewButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.agendaView === view)));
    form.closest('details').hidden = next !== 'day';
    document.getElementById('agendaPrev').setAttribute('aria-label', 'Previous ' + view);
    document.getElementById('agendaNext').setAttribute('aria-label', 'Next ' + view);
    load();
  }

  function period(day) {
    const start = new Date(day + 'T12:00');
    if (view === 'day') return { start: day, days: 1, title: dateTitle.format(start) };
    if (view === 'week') {
      start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
      const end = new Date(start);
      end.setDate(end.getDate() + 6);
      return { start: dateValue(start), days: 7, title: shortDate.format(start) + ' to ' + shortDate.format(end) };
    }
    start.setDate(1);
    const days = new Date(start.getFullYear(), start.getMonth() + 1, 0).getDate();
    return { start: dateValue(start), days, title: monthTitle.format(start) };
  }

  function dayButton(day, label) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.setAttribute('aria-label', dateTitle.format(new Date(day + 'T12:00')));
    if (day === today()) button.setAttribute('aria-current', 'date');
    button.addEventListener('click', () => {
      changeDay(day, false);
      selectView('day');
      date.focus();
    });
    return button;
  }

  function renderWeek(days) {
    return days.map(day => {
      const section = document.createElement('section');
      section.className = 'agenda-week-day';
      const label = document.createElement('h3');
      label.appendChild(dayButton(day.date, dateTitle.format(new Date(day.date + 'T12:00'))));
      section.appendChild(label);
      if (day.exams.length) {
        const note = document.createElement('p');
        note.textContent = day.exams.map(exam => exam.title + ' tomorrow').join(' · ');
        section.appendChild(note);
      }
      const rows = document.createElement('ol');
      rows.className = 'agenda-items';
      rows.append(...day.items.map(item => renderItem(item, day.date)));
      if (day.items.length) section.appendChild(rows);
      else {
        const empty = document.createElement('p');
        empty.textContent = 'Open day';
        section.appendChild(empty);
      }
      return section;
    });
  }

  function renderMonth(days) {
    const first = new Date(days[0].date + 'T12:00');
    const cells = [];
    for (let index = 0; index < 7; index++) {
      const label = document.createElement('span');
      label.className = 'agenda-month-weekday';
      label.textContent = shortDay.format(new Date(2026, 0, 5 + index));
      cells.push(label);
    }
    for (let index = 0; index < (first.getDay() + 6) % 7; index++) {
      const spacer = document.createElement('span');
      spacer.setAttribute('aria-hidden', 'true');
      cells.push(spacer);
    }
    for (const day of days) {
      const cell = dayButton(day.date, String(Number(day.date.slice(-2))));
      const count = day.items.filter(item => item.kind !== 'break').length;
      if (count || day.exams.length) {
        const detail = document.createElement('span');
        detail.className = 'agenda-month-count';
        detail.textContent = day.exams.length ? 'Exam prep' : count + (count === 1 ? ' item' : ' items');
        cell.setAttribute('aria-label', cell.getAttribute('aria-label') + ', ' + detail.textContent);
        cell.appendChild(detail);
      }
      cells.push(cell);
    }
    return cells;
  }

  async function load() {
    if (dayRequest) dayRequest.abort();
    if (!date.value) return;
    const day = date.value;
    const selection = period(day);
    const request = new AbortController();
    dayRequest = request;
    heading.textContent = selection.title;
    status.textContent = 'Loading calendar…';
    items.replaceChildren();
    exams.hidden = true;
    range.hidden = true;
    range.replaceChildren();
    try {
      const data = await readDay(selection.start, request.signal, undefined, selection.days);
      if (request.signal.aborted) return;
      status.textContent = data.calendar_unavailable ? 'Some connected calendars could not refresh.' : '';
      if (view === 'day') {
        items.replaceChildren(...data.items.map(item => renderItem(item, day)));
        renderExams(data.exams, day);
        if (!data.calendar_unavailable && !data.items.length) status.textContent = 'Nothing scheduled for this day.';
      } else {
        range.className = 'agenda-' + view;
        range.replaceChildren(...(view === 'month' ? renderMonth(data.days) : renderWeek(data.days)));
        range.hidden = false;
      }
    } catch (error) {
      if (!request.signal.aborted) status.textContent = error.message || 'Could not load your calendar.';
    }
  }

  function changeDay(day, refresh = true) {
    date.value = day;
    if (slotRequest) slotRequest.abort();
    form.elements.start.value = '';
    form.elements.end.value = '';
    activityStatus.textContent = '';
    if (refresh) load();
  }

  function shiftDay(delta) {
    const next = new Date((date.value || today()) + 'T12:00');
    if (view === 'month') {
      const original = next.getDate();
      next.setDate(1);
      next.setMonth(next.getMonth() + delta);
      const last = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
      next.setDate(Math.min(original, last));
    } else {
      next.setDate(next.getDate() + (view === 'week' ? 7 : 1) * delta);
    }
    changeDay(dateValue(next));
  }

  viewButtons.forEach(button => button.addEventListener('click', () => selectView(button.dataset.agendaView)));
  date.addEventListener('change', () => changeDay(date.value || today()));
  document.getElementById('agendaPrev').addEventListener('click', () => shiftDay(-1));
  document.getElementById('agendaNext').addEventListener('click', () => shiftDay(1));
  document.getElementById('agendaToday').addEventListener('click', () => changeDay(today()));
  document.addEventListener('scheduler:calendar', load);

  find.addEventListener('click', async () => {
    const request = new AbortController();
    slotRequest = request;
    const day = date.value;
    find.disabled = true;
    activityStatus.textContent = 'Checking for open time…';
    try {
      const data = await readDay(day, request.signal, form.elements.duration.value);
      if (request.signal.aborted) return;
      if (!data.opening) {
        activityStatus.textContent = 'No opening for that length. Try a shorter activity or a different day.';
        return;
      }
      form.elements.start.value = data.opening.start;
      form.elements.end.value = data.opening.end;
      activityStatus.textContent = 'Opening found. Review the time and add your activity.';
    } catch (error) {
      if (!request.signal.aborted) activityStatus.textContent = error.message;
    } finally {
      find.disabled = false;
    }
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!date.reportValidity()) return;
    const day = date.value;
    const { title, start, end } = form.elements;
    if (end.value <= start.value) {
      activityStatus.textContent = 'Choose an end time after the start time.';
      return;
    }
    const destination = event.submitter?.value || 'google';
    const buttons = form.querySelectorAll('button');
    if (slotRequest) slotRequest.abort();
    buttons.forEach(button => { button.disabled = true; });
    activityStatus.textContent = destination === 'google' ? 'Adding activity…' : 'Preparing calendar file…';
    try {
      const response = await fetch('/api/calendar/personal-events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Timezone': timezone },
        body: JSON.stringify({
          events: [{ title: title.value, date: day, start: start.value, end: end.value }],
          destination,
        }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.message || 'Could not add this activity.');
      }
      if (destination === 'google') {
        const data = await response.json();
        if (date.value === day) {
          activityStatus.textContent = data.created ? 'Added to Google Calendar.' : 'This activity is already on Google Calendar.';
          form.reset();
        }
        load();
      } else {
        const url = URL.createObjectURL(await response.blob());
        const download = document.createElement('a');
        download.href = url;
        download.download = 'intelliplan-activity.ics';
        download.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        activityStatus.textContent = 'Open the downloaded file in your calendar to add this activity.';
      }
    } catch (error) {
      activityStatus.textContent = error.message;
    } finally {
      buttons.forEach(button => { button.disabled = false; });
    }
  });

  if (params.get('view') === 'calendar') tab.click();
  else if (tab.getAttribute('aria-selected') === 'true') load();
}());
