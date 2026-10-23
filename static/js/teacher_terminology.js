(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const state = { classrooms: [], vocabulary: [] };
  const status = message => { byId('termsStatus').textContent = message; };
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || 'Classroom settings could not be loaded. Reload and try again.');
    return data;
  }
  function render() {
    const row = state.classrooms.find(item => String(item.id) === byId('termsClass').value);
    byId('termsChoices').replaceChildren();
    byId('termsRestrictedChoices').replaceChildren();
    byId('termsRestricted').open = false;
    if (!row) return;
    for (const term of state.vocabulary) {
      const label = document.createElement('label');
      label.style.cssText = 'display:flex;align-items:center;gap:10px;min-height:44px';
      const input = document.createElement('input');
      input.type = 'checkbox'; input.value = term;
      input.checked = row.terms.includes(term) && row.permitted_terms.includes(term);
      input.disabled = !row.permitted_terms.includes(term);
      input.style.minHeight = '20px';
      label.append(input, document.createTextNode(term + (input.disabled ? ' · institution restricted' : '')));
      byId(input.disabled ? 'termsRestrictedChoices' : 'termsChoices').append(label);
    }
    const restricted = state.vocabulary.filter(term => !row.permitted_terms.includes(term)).length;
    byId('termsRestricted').hidden = !restricted;
    byId('termsRestrictedSummary').textContent = restricted + ' terms restricted by your institution';
  }
  async function load() {
    byId('termsRetry').disabled = true;
    byId('termsForm').hidden = true;
    byId('termsRetry').hidden = true;
    status('Loading your assigned classrooms…');
    try {
      const data = await api('/api/teacher/terminology');
      state.classrooms = data.classrooms; state.vocabulary = data.vocabulary;
      byId('termsClass').replaceChildren();
      for (const row of state.classrooms) {
        const option = document.createElement('option'); option.value = String(row.id); option.textContent = row.name;
        byId('termsClass').append(option);
      }
      byId('termsForm').hidden = !state.classrooms.length;
      status(state.classrooms.length ? 'Choose a classroom, then select the vocabulary for its lessons.' : 'No classroom policy access has been assigned. Ask your institution administrator to assign you as a classroom teacher. Student-progress invitations do not grant policy access.');
      render();
    } catch (error) { status(error.message); byId('termsRetry').hidden = false; }
    finally { byId('termsRetry').disabled = false; }
  }
  byId('termsClass').addEventListener('change', render);
  byId('termsRetry').addEventListener('click', load);
  byId('termsForm').addEventListener('submit', async event => {
    event.preventDefault();
    const row = state.classrooms.find(item => String(item.id) === byId('termsClass').value);
    if (!row) return;
    const terms = [...byId('termsChoices').querySelectorAll('input:checked:not(:disabled)')].map(input => input.value);
    byId('termsSave').disabled = true;
    byId('termsClass').disabled = true;
    byId('termsFields').disabled = true;
    byId('termsRetry').disabled = true;
    try {
      const result = await api('/api/teacher/terminology/' + row.id, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ terms, version: row.version })
      });
      row.version = result.version; row.terms = result.terms;
      status('Classroom terms saved. Safety checks and institution restrictions still apply.');
    } catch (error) { status(error.message); byId('termsRetry').hidden = false; }
    finally {
      byId('termsSave').disabled = false; byId('termsClass').disabled = false;
      byId('termsFields').disabled = false; byId('termsRetry').disabled = false;
    }
  });
  load();
})();
