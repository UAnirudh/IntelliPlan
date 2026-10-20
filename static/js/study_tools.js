'use strict';
(() => {
  const root = document.querySelector('.study-tools');
  if (!root) return;
  const search = document.getElementById('studyToolSearch');
  const filters = [...root.querySelectorAll('[data-tool-filter]')];
  let category = 'all';
  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    let count = 0;
    root.querySelectorAll('[data-tool-group]').forEach(group => {
      let visible = 0;
      group.querySelectorAll('[data-tool-name]').forEach(tool => {
        tool.hidden = !(category === 'all' || category === group.dataset.toolGroup)
          || !tool.dataset.toolName.toLocaleLowerCase().includes(query);
        if (!tool.hidden) visible++;
      });
      group.hidden = visible === 0;
      count += visible;
    });
    document.getElementById('studyToolsCount').textContent = `${count} ${count === 1 ? 'tool' : 'tools'} found.`;
    document.getElementById('studyToolsEmpty').hidden = count > 0;
  }
  filters.forEach(button => button.addEventListener('click', () => {
    category = button.dataset.toolFilter;
    filters.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    filter();
  }));
  search.addEventListener('input', filter);
  root.querySelector('.study-tools-controls').hidden = false;
  filter();
})();
