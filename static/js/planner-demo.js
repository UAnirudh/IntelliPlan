'use strict';

(() => {
  const examples = [
    { title: 'Review fraction exercises', course: 'Math', minutes: 25 },
    { title: 'Outline a science poster', course: 'Science', minutes: 25 },
    { title: 'Read a history chapter', course: 'History', minutes: 20 }
  ];
  const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday'];
  const select = document.getElementById('demo-minutes');
  const status = document.getElementById('demo-status');
  const empty = document.getElementById('demo-empty');
  const adjust = document.getElementById('demo-adjust');
  const tabs = [...document.querySelectorAll('.sched-tab')];
  const tabPanels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
  let tasks = [];
  let plan = [];
  let unplaced = [];
  let firstAvailableDay = 0;

  function showTab(index, focus = false) {
    tabs.forEach((tab, position) => {
      const isSelected = position === index;
      tab.setAttribute('aria-selected', String(isSelected));
      tab.classList.toggle('active', isSelected);
      tab.tabIndex = isSelected ? 0 : -1;
      tabPanels[position].hidden = !isSelected;
    });
    if (focus) tabs[index].focus();
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => showTab(index));
    tab.addEventListener('keydown', event => {
      const destination = { ArrowLeft: 1 - index, ArrowRight: 1 - index, Home: 0, End: 1 }[event.key];
      if (destination === undefined) return;
      event.preventDefault();
      showTab(destination, true);
    });
  });

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
    for (let day = days.length - 1; day >= 0; day -= 1) {
      const blocks = plan.filter(block => block.day === day && !block.task.done);
      if (!blocks.length) continue;
      const section = text('section', '', 'iv-day-card');
      const header = text('div', '', 'iv-day-head');
      const minutes = blocks.reduce((sum, block) => sum + block.task.minutes, 0);
      header.append(text('h3', days[day], 'iv-day-name'), text('span', `${minutes} min`, 'iv-day-meta'));
      section.append(header);
      const list = text('ol', '', 'demo-blocks');
      for (const block of blocks) {
        const item = text('li', '', 'iv-block');
        const body = text('div', '', 'iv-block-body');
        body.append(text('div', block.task.title, 'iv-block-title'), text('div', block.task.course, 'iv-block-sub'));
        if (block.task.missed) body.append(text('span', 'Missed · Find a later slot with Adjust remaining work.', 'missed'));
        const right = text('div', '', 'iv-block-right');
        right.append(text('span', `${block.task.minutes} min`, 'iv-duration'));
        item.append(text('span', `${clock(block.start)} – ${clock(block.start + block.task.minutes)}`, 'iv-time'), body, right);
        const actions = text('div', '', 'block-actions');
        const done = text('button', 'Mark done', 'secondary');
        done.type = 'button';
        done.setAttribute('aria-label', `Mark complete: ${block.task.title}`);
        done.addEventListener('click', () => {
          block.task.done = true;
          block.task.completedDay = block.day;
          block.task.completedEnd = block.start + block.task.minutes;
          firstAvailableDay = Math.max(firstAvailableDay, block.day);
          render();
          status.textContent = `${block.task.title} marked complete. ${tasks.filter(task => !task.done).length} example tasks remain.`;
          adjust.focus();
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
    const count = document.getElementById('ivBlockCount');
    const activeBlocks = plan.filter(block => !block.task.done).length;
    count.textContent = activeBlocks;
    count.hidden = !activeBlocks;
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
    empty.hidden = true;
    adjust.hidden = false;
    render();
    status.textContent = `${plan.length} unfinished tasks scheduled. ${unplaced.length} could not be scheduled.`;
    showTab(1);
    document.getElementById('plan-heading').focus();
  }

  function reset() {
    tasks = examples.map(task => ({ ...task, done: false, missed: false, earliestDay: 0 }));
    plan = [];
    unplaced = [];
    firstAvailableDay = 0;
    select.value = '30';
    empty.hidden = false;
    adjust.hidden = true;
    render();
    showTab(0);
    status.textContent = '';
  }

  document.getElementById('demo-build').addEventListener('click', build);
  adjust.addEventListener('click', build);
  const resetButton = document.getElementById('demo-reset');
  resetButton.addEventListener('click', () => {
    reset();
    status.textContent = 'Sample plan reset.';
    select.focus();
  });
  document.getElementById('demo-tabs').hidden = false;
  resetButton.hidden = false;
  reset();
})();
