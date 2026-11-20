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
  const clock = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const labels = { class: 'Class', study: 'Study', break: 'Break', busy: 'Calendar', commitment: 'Activity' };

  let dayRequest;
  let slotRequest;
  date.value = params.get('date') || today();
  if (!date.value) date.value = today();

  async function readDay(day, signal, duration) {
    const query = new URLSearchParams({ date: day, tz: timezone });
    if (duration) query.set('duration', duration);
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

  async function load() {
    if (dayRequest) dayRequest.abort();
    if (!date.value) return;
    const day = date.value;
    const request = new AbortController();
    dayRequest = request;
    heading.textContent = dateTitle.format(new Date(day + 'T12:00'));
    status.textContent = 'Loading calendar…';
    items.replaceChildren();
    exams.hidden = true;
    try {
      const data = await readDay(day, request.signal);
      if (request.signal.aborted) return;
      items.replaceChildren(...data.items.map(item => renderItem(item, day)));
      renderExams(data.exams, day);
      status.textContent = data.calendar_unavailable ? 'Some connected calendars could not refresh.' :
        data.items.length ? '' : 'Nothing scheduled for this day.';
    } catch (error) {
      if (!request.signal.aborted) status.textContent = error.message || 'Could not load your calendar.';
    }
  }

  function changeDay(day) {
    date.value = day;
    if (slotRequest) slotRequest.abort();
    form.elements.start.value = '';
    form.elements.end.value = '';
    activityStatus.textContent = '';
    load();
  }

  function shiftDay(delta) {
    const next = new Date((date.value || today()) + 'T12:00');
    next.setDate(next.getDate() + delta);
    changeDay(dateValue(next));
  }

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
