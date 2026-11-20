(() => {
  'use strict';
  const button = document.getElementById('runConnectionCheck');
  const results = document.getElementById('connectionCheckResults');
  if (!button || !results) return;

  const labels = {
    canvas: 'Canvas', calendar_feed: 'School calendar feed', studentvue: 'StudentVue',
    schoology: 'Schoology', hac: 'Home Access Center', google_classroom: 'Google Classroom',
    blackboard: 'Blackboard', moodle: 'Moodle',
  };

  async function read(url) {
    const response = await fetch(url, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || 'Connection check failed.');
    return data;
  }

  async function school() {
    const [account, oauth] = await Promise.all([
      read('/api/settings/lms-sources'),
      read('/oauth/canvas/status').catch(() => null),
    ]);
    const providers = new Set(account.linked_accounts.map(item => item.login_type));
    for (const [provider, linked] of Object.entries(account.integrations || {})) {
      if (linked) providers.add(provider);
    }
    if (oauth?.connected) providers.add('canvas');
    if (!providers.size) return [['School accounts', 'No account linked']];
    return [...providers].filter(provider => labels[provider]).map(provider => [
      labels[provider], provider === 'canvas' && oauth?.needs_reconnect ? 'Reconnect to resume sync' : 'Linked',
    ]);
  }

  async function calendars() {
    const state = await read('/calendar/connections');
    const linked = [(state.google || state.google_linked) && 'Google Calendar', state.outlook && 'Outlook'].filter(Boolean);
    if (!linked.length) return [['Calendars', 'No calendar linked']];
    const check = await read('/calendar/free-slot').catch(() => null);
    const status = check?.calendar_verified ? 'Availability verified' : 'Availability could not be verified';
    return [[linked.join(' and '), status]];
  }

  async function notion() {
    const status = await read('/notion/status');
    if (!status.configured) return [['Notion', 'Unavailable on this deployment']];
    if (!status.connected) return [['Notion', 'Not linked']];
    if (!status.has_database) return [['Notion', 'Choose a task database in Settings']];
    const data = await read('/notion/upcoming?days=1');
    return [['Notion', data.connected ? 'Task database verified' : 'Could not read the selected database']];
  }

  async function todoist() {
    const status = await read('/api/todoist/connection');
    return [['Todoist', status.connected ? 'Projects verified' : status.available ? 'Not linked' : 'Unavailable for this account']];
  }

  function append(name, status) {
    const row = document.createElement('li');
    const label = document.createElement('strong');
    label.textContent = name;
    const description = document.createElement('span');
    description.textContent = status;
    row.append(label, description);
    results.appendChild(row);
  }

  button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = 'Checking…';
    results.replaceChildren();
    try {
      const checks = [['School accounts', school], ['Calendars', calendars], ['Notion', notion], ['Todoist', todoist]];
      const values = await Promise.allSettled(checks.map(([, check]) => check()));
      values.forEach((value, index) => {
        const rows = value.status === 'fulfilled' && value.value.length
          ? value.value : [[checks[index][0], 'Could not check. Try again.']];
        for (const [name, message] of rows) append(name, message);
      });
      results.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = 'Check again';
    }
  });
})();
