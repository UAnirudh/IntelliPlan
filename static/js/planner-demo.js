'use strict';

(() => {
  const examples = [
    { id: 'fractions', title: 'Review fictional fraction exercises', minutes: 25 },
    { id: 'poster', title: 'Outline an imaginary science poster', minutes: 25 },
    { id: 'reading', title: 'Read a sample history chapter', minutes: 20 }
  ];
  const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday'];
  const select = document.getElementById('demo-minutes');
  const status = document.getElementById('demo-status');
  const result = document.getElementById('demo-result');
  const adjust = document.getElementById('demo-adjust');
  let tasks = [];
  let plan = [];
  let unplaced = [];
  let firstAvailableDay = 0;

  function text(tag, content, className) {
    const element = document.createElement(tag);
    element.textContent = content;
    if (className) element.className = className;
    return element;
  }

  function clock(offset) {
    const hour = 4 + Math.floor(offset / 60);
    return `${hour}:${String(offset % 60).padStart(2, '0')} pm`;
  }

  function render() {
    const container = document.getElementById('demo-days');
    container.replaceChildren();
    for (let day = 0; day < days.length; day += 1) {
      const blocks = plan.filter(block => block.day === day && !block.task.done);
      if (!blocks.length) continue;
      const section = text('section', '', 'demo-day');
      section.append(text('h3', days[day]));
      const list = document.createElement('ol');
      for (const block of blocks) {
        const item = text('li', block.task.title);
        item.append(text('span', `${clock(block.start)} to ${clock(block.start + block.task.minutes)}`, 'block-time'));
        if (block.task.missed) item.append(text('span', 'Missed. Choose Adjust remaining work to find a later slot.', 'missed'));
        const actions = text('div', '', 'block-actions');
        const done = text('button', 'Mark complete');
        done.type = 'button';
        done.setAttribute('aria-label', `Mark complete: ${block.task.title}`);
        done.addEventListener('click', () => {
          block.task.done = true;
          block.task.completedDay = block.day;
          block.task.completedEnd = block.start + block.task.minutes;
          firstAvailableDay = Math.max(firstAvailableDay, block.day);
          render();
          status.textContent = `${block.task.title} marked complete. ${tasks.filter(task => !task.done).length} example tasks remain.`;
          document.getElementById('demo-adjust').focus();
        });
        const miss = text('button', 'Mark missed', 'secondary');
        miss.type = 'button';
        miss.setAttribute('aria-label', `Mark missed: ${block.task.title}`);
        miss.disabled = block.task.missed;
        miss.addEventListener('click', () => {
          block.task.missed = true;
          block.task.earliestDay = block.day + 1;
          firstAvailableDay = Math.max(firstAvailableDay, block.day + 1);
          render();
          status.textContent = `${block.task.title} marked missed. Adjust remaining work to move it to a later day.`;
          adjust.focus();
        });
        actions.append(done, miss);
        item.append(actions);
        list.append(item);
      }
      section.append(list);
      container.append(section);
    }
    const leftover = document.getElementById('demo-unplaced');
    leftover.replaceChildren();
    if (unplaced.length) {
      leftover.append(text('h3', 'Work that does not fit before the example deadline'));
      const list = document.createElement('ul');
      unplaced.forEach(task => list.append(text('li', task.title)));
      leftover.append(list, text('p', 'This example has no later available slot before Thursday at 5 pm. More time or a changed deadline would be needed. No task has been silently removed.'));
    }
    const completed = document.getElementById('demo-completed');
    completed.replaceChildren();
    const finished = tasks.filter(task => task.done);
    if (finished.length) {
      completed.append(text('h3', 'Completed example work'));
      const list = document.createElement('ul');
      finished.forEach(task => list.append(text('li', task.title)));
      completed.append(list);
    }
  }

  function build() {
    const capacity = select.value === '60' ? 60 : 30;
    const used = days.map(() => 0);
    for (const task of tasks.filter(task => task.done)) {
      used[task.completedDay] = Math.max(used[task.completedDay], task.completedEnd);
    }
    plan = [];
    unplaced = [];
    for (const task of tasks) {
      if (task.done) continue;
      let placed = false;
      for (let day = Math.max(firstAvailableDay, task.earliestDay); day < days.length; day += 1) {
        const start = used[day] ? used[day] + 5 : 0;
        if (start + task.minutes > capacity) continue;
        plan.push({ task, day, start });
        used[day] = start + task.minutes;
        task.missed = false;
        placed = true;
        break;
      }
      if (!placed) unplaced.push(task);
    }
    result.hidden = false;
    adjust.hidden = false;
    render();
    status.textContent = `${plan.length} unfinished example tasks scheduled; ${unplaced.length} do not fit. Completed work stays complete.`;
    document.getElementById('plan-heading').focus();
  }

  function reset() {
    tasks = examples.map(task => ({ ...task, done: false, missed: false, earliestDay: 0 }));
    plan = [];
    unplaced = [];
    firstAvailableDay = 0;
    select.value = '30';
    result.hidden = true;
    adjust.hidden = true;
    status.textContent = 'Demo reset. Choose availability, then build the example plan.';
  }

  document.getElementById('demo-build').addEventListener('click', build);
  adjust.addEventListener('click', build);
  document.getElementById('demo-reset').addEventListener('click', () => { reset(); select.focus(); });
  reset();
})();
