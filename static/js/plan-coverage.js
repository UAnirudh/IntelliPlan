(() => {
  'use strict';

  const datePrefix = /^\d{4}-\d{2}-\d{2}(?:$|T)/;

  function summarize(plan) {
    const grouped = new Map();
    for (const item of Array.isArray(plan.deferred) ? plan.deferred : []) {
      if (!item || typeof item !== 'object') continue;
      const deadline = [item.deadline_at, item.due_date].find(value => typeof value === 'string' && datePrefix.test(value)) || '';
      const title = typeof item.title === 'string' && item.title.trim() ? item.title : 'Assignment';
      const key = item.task_id ? JSON.stringify([item.task_id, deadline.slice(0, 10)]) : Symbol();
      const task = grouped.get(key) || { title, deadline, minutes: 0, reasons: [] };
      if (deadline.length > 10 && (task.deadline.length === 10 || deadline < task.deadline)) {
        task.deadline = deadline;
      }
      const minutes = Number(item.minutes);
      if (Number.isFinite(minutes) && minutes > 0) task.minutes += Math.round(minutes);
      if (typeof item.reason === 'string' && item.reason.trim() && !task.reasons.includes(item.reason)) task.reasons.push(item.reason);
      grouped.set(key, task);
    }
    const tasks = [...grouped.values()].sort((a, b) => (a.deadline || '9999').localeCompare(b.deadline || '9999'));
    return { tasks, minutes: tasks.reduce((sum, task) => sum + task.minutes, 0) };
  }

  function duration(minutes) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return [hours && hours + 'h', rest && rest + 'm'].filter(Boolean).join(' ');
  }

  function due(deadline) {
    const day = deadline.slice(0, 10);
    const clock = /^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})/.exec(deadline);
    if (!clock || clock[1] === '23' && clock[2] === '59') return day;
    const hour = Number(clock[1]);
    return day + ' at ' + (hour % 12 || 12) + ':' + clock[2] + (hour < 12 ? ' AM' : ' PM');
  }

  window.IPPlanCoverage = { summarize, duration, due };
})();
