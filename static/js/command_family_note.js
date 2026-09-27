(() => {
  'use strict';
  const card = document.getElementById('commandFamilyNote');
  const message = document.getElementById('commandFamilyMessage');
  const seen = document.getElementById('commandFamilySeen');
  if (!card || !message || !seen) return;
  let noteId = null;
  fetch('/api/roles/my-encouragement', { credentials: 'same-origin' })
    .then((response) => response.ok ? response.json() : null)
    .then((data) => {
      const note = data?.notes?.[0];
      if (!note) return;
      noteId = note.id;
      message.textContent = note.message;
      card.hidden = false;
    }).catch(() => {});
  seen.addEventListener('click', async () => {
    if (!noteId) return;
    seen.disabled = true;
    try {
      const response = await fetch(`/api/roles/my-encouragement/${noteId}/acknowledge`,
        { method: 'POST', credentials: 'same-origin' });
      if (!response.ok) throw new Error();
      card.hidden = true;
    } catch (_) { seen.disabled = false; }
  });
})();
