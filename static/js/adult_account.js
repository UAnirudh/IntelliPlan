(() => {
  'use strict';
  const dialog = document.getElementById('adultDeleteDialog');
  const confirm = document.getElementById('adultDeleteConfirm');
  const cancel = document.getElementById('adultDeleteCancel');
  const status = document.getElementById('adultDeleteStatus');
  let pending = false;
  document.getElementById('adultDeleteOpen').addEventListener('click', () => {
    status.textContent = '';
    dialog.showModal();
  });
  cancel.addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => { if (pending) event.preventDefault(); });
  confirm.addEventListener('click', async () => {
    if (pending) return;
    pending = true;
    confirm.disabled = cancel.disabled = true;
    status.textContent = 'Deleting your account…';
    try {
      const response = await fetch('/account/delete', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }
      });
      const data = await response.json();
      if (!response.ok || data.status !== 'ok') throw new Error('Account deletion did not complete. Please try again.');
      window.location.assign('/');
    } catch (_) {
      status.textContent = 'Account deletion did not complete. Please try again.';
      pending = false;
      confirm.disabled = cancel.disabled = false;
    }
  });
})();
