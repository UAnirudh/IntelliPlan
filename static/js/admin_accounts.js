(() => {
  'use strict';
  const status = document.getElementById('adminAccountStatus');
  const results = document.getElementById('adminAccountResults');
  async function load(email = '') {
    try {
      const response = await fetch(`/api/admin/accounts${email ? '?email=' + encodeURIComponent(email) : ''}`);
      if (!response.ok) throw new Error('Account access could not be loaded.');
      const data = await response.json();
      results.replaceChildren();
      for (const account of data.accounts) {
        const card = document.createElement('section');
        card.className = 'glass-card';
        card.style.cssText = 'padding:24px;border-radius:24px;display:flex;align-items:center;gap:18px;flex-wrap:wrap';
        const identity = document.createElement('strong');
        identity.style.overflowWrap = 'anywhere';
        identity.textContent = `${account.name || account.email} · Account #${account.id}`;
        const button = document.createElement('button');
        button.className = 'btn-secondary';
        button.type = 'button';
        button.textContent = account.owner ? 'Owner' : account.admin ? 'Revoke admin access' : 'Grant admin access';
        button.disabled = account.owner;
        button.addEventListener('click', async () => {
          button.disabled = true;
          try {
            const saved = await fetch('/api/admin/accounts', { method: account.admin ? 'DELETE' : 'POST',
              headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: account.id }) });
            if (!saved.ok) throw new Error('Access could not be updated.');
            await load(email);
            status.textContent = 'Account access updated. Revocation applies to existing sessions.';
          } catch (error) { status.textContent = error.message; button.disabled = false; }
        });
        card.append(identity, button);
        results.append(card);
      }
      status.textContent = data.accounts.length ? 'Review the account before changing access.' : 'No accounts found.';
    } catch (error) { status.textContent = error.message; }
  }
  document.getElementById('adminAccountSearch').addEventListener('submit', (event) => {
    event.preventDefault(); load(document.getElementById('adminAccountEmail').value.trim());
  });
  load();
})();
