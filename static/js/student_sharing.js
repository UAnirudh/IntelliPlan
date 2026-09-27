(() => {
  'use strict';
  const byId = (id) => document.getElementById(id);
  const status = (message) => { byId('sharingStatus').textContent = message; };
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Could not update sharing.');
    return data;
  }
  function row(link) {
    const card = document.createElement('div');
    card.className = 'sharing-row';
    const identity = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = link.linker_name;
    const detail = document.createElement('small');
    detail.textContent = `${link.relationship === 'parent' ? 'Family' : 'Teacher'} · ${link.linker_email}`;
    identity.append(title, detail);
    const actions = document.createElement('div');
    actions.className = 'sharing-actions';
    if (!link.accepted) {
      const accept = document.createElement('button');
      accept.className = 'btn-primary';
      accept.type = 'button';
      accept.textContent = 'Allow summary';
      accept.addEventListener('click', () => update(link.link_id, 'accept'));
      actions.append(accept);
    }
    const remove = document.createElement('button');
    remove.className = 'sharing-secondary';
    remove.type = 'button';
    remove.textContent = link.accepted ? 'Revoke access' : 'Decline';
    remove.addEventListener('click', () => update(link.link_id, 'remove'));
    actions.append(remove);
    card.append(identity, actions);
    return card;
  }
  async function load() {
    try {
      const { links } = await api('/api/roles/my-links');
      for (const [id, accepted, empty] of [
        ['sharingPending', false, 'No requests waiting.'],
        ['sharingActive', true, 'No one can view your summary.'],
      ]) {
        const host = byId(id);
        host.replaceChildren();
        const matching = links.filter((link) => link.accepted === accepted);
        if (!matching.length) {
          const p = document.createElement('p');
          p.className = 'sharing-empty';
          p.textContent = empty;
          host.append(p);
        } else matching.forEach((link) => host.append(row(link)));
      }
      status('You control these links.');
    } catch (error) { status(error.message); }
  }
  async function update(id, action) {
    status('Updating access…');
    try {
      await api(action === 'accept' ? `/api/roles/links/${id}/accept` : `/api/roles/links/${id}`,
        { method: action === 'accept' ? 'POST' : 'DELETE' });
      await load();
    } catch (error) { status(error.message); }
  }
  load();
})();
