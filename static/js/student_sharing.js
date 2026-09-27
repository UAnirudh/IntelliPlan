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
    if (link.relationship === 'parent') {
      const choices = document.createElement('fieldset');
      choices.className = 'sharing-scopes';
      const legend = document.createElement('legend');
      legend.textContent = 'Choose what they can see';
      choices.append(legend);
      for (const [scope, label] of [
        ['work', 'Assignments'], ['study', 'Recorded study'], ['foundations', 'Foundations practice'],
      ]) {
        const wrapper = document.createElement('label');
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = scope;
        input.dataset.shareScope = scope;
        input.checked = (link.scopes || []).includes(scope);
        wrapper.append(input, document.createTextNode(label));
        choices.append(wrapper);
      }
      identity.append(choices);
    }
    const selectedScopes = () => [...card.querySelectorAll('[data-share-scope]:checked')]
      .map((input) => input.value);
    const actions = document.createElement('div');
    actions.className = 'sharing-actions';
    if (!link.accepted) {
      const accept = document.createElement('button');
      accept.className = 'btn-primary';
      accept.type = 'button';
      accept.textContent = 'Allow selected summary';
      accept.addEventListener('click', () => update(link.link_id, 'accept',
        link.relationship === 'parent' ? selectedScopes() : null));
      actions.append(accept);
    } else if (link.relationship === 'parent') {
      const save = document.createElement('button');
      save.className = 'btn-primary';
      save.type = 'button';
      save.textContent = 'Save sharing choices';
      save.addEventListener('click', () => update(link.link_id, 'scopes', selectedScopes()));
      actions.append(save);
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
  async function update(id, action, scopes = null) {
    if (scopes && !scopes.length) {
      status('Choose at least one category, or decline or revoke the link.');
      return;
    }
    status('Updating access…');
    try {
      const path = action === 'accept' ? `/api/roles/links/${id}/accept`
        : action === 'scopes' ? `/api/roles/links/${id}/scopes` : `/api/roles/links/${id}`;
      const options = { method: action === 'accept' ? 'POST' : action === 'scopes' ? 'PATCH' : 'DELETE' };
      if (scopes) {
        options.headers = { 'Content-Type': 'application/json' };
        options.body = JSON.stringify({ scopes });
      }
      await api(path, options);
      await load();
    } catch (error) { status(error.message); }
  }
  load();
})();
