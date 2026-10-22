(() => {
  'use strict';
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function value(summary, key, source) {
    if (summary[source + '_status'] === 'not_shared') return 'Private';
    if (!['ok', 'partial'].includes(summary[source + '_status'])) return 'Unavailable';
    return Number.isFinite(summary[key]) && summary[key] >= 0 ? summary[key] : '—';
  }
  function render(host, summary) {
    host.replaceChildren();
    const tasks = element('section', null, 'adult-progress-work');
    tasks.append(element('h3', 'Shared tasks'));
    const completed = value(summary, 'completed', 'work');
    const open = value(summary, 'open', 'work');
    if (typeof completed === 'number' && typeof open === 'number') {
      const total = completed + open;
      tasks.append(element('p', total ? completed + ' of ' + total + ' shared tasks marked complete' : 'No shared tasks recorded yet.'));
      if (total) {
        const bar = element('progress');
        bar.max = total;
        bar.value = completed;
        bar.setAttribute('aria-label', 'Shared tasks marked complete');
        tasks.append(bar);
      }
    } else tasks.append(element('p', completed === 'Private' ? 'The student has not shared task progress.' : 'Task progress is temporarily unavailable.'));
    tasks.append(element('p', 'Tasks marked complete in IntelliPlan; this is not a course grade. Dismissed tasks do not count as completed.', 'adult-progress-note'));
    const study = element('section', null, 'adult-progress-study');
    study.append(element('h3', 'Recorded practice · past 7 days'));
    if (summary.study_status === 'ok' && Array.isArray(summary.study_week)) {
      const week = element('ol', null, 'adult-study-week');
      week.setAttribute('aria-label', 'Recorded study sessions in the past seven days');
      const max = Math.max(1, ...summary.study_week.map(day => Number.isFinite(day.sessions) ? day.sessions : 0));
      summary.study_week.forEach(day => {
        const count = Number.isFinite(day.sessions) && day.sessions >= 0 ? day.sessions : 0;
        const row = element('li');
        const date = element('time', new Date(day.date + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short' }));
        date.dateTime = day.date;
        const bar = element('progress');
        bar.max = max;
        bar.value = count;
        bar.setAttribute('aria-label', day.date + ': ' + count + (count === 1 ? ' session' : ' sessions'));
        row.append(date, bar, element('span', String(count)));
        week.append(row);
      });
      study.append(week);
    } else study.append(element('p', summary.study_status === 'not_shared' ? 'The student has not shared recorded practice.' : 'Recorded practice is temporarily unavailable.'));
    study.append(element('p', 'These are recorded sessions, not a measure of all studying or learning.', 'adult-progress-note'));
    host.append(tasks, study);
  }
  window.AdultProgress = { value, render };
})();
