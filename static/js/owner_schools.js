(() => {
  const root = document.getElementById('ownerSchools');
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[ch]);
  const state = { schools: [], tiers: [], selected: null, proof: null };
  const notice = (node, message, kind = '') => {
    node.textContent = message;
    node.className = `owner-notice ${kind}`;
  };
  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || data.error || `Request failed (${response.status})`);
    return data;
  }
  function renderList() {
    const list = $('schoolList');
    list.innerHTML = state.schools.length ? state.schools.map((school) => `
      <button class="owner-row" type="button" data-school="${school.id}" aria-current="${school.id === state.selected ? 'true' : 'false'}">
        <span class="owner-row-top"><strong>${esc(school.name)}</strong><span class="owner-pill">${esc(school.kind)}</span></span>
        <small>${school.members.members} members · ${school.domains.filter((d) => d.verified).length} verified domains · ${esc(school.plan_tier)}</small>
      </button>`).join('') : '<div class="owner-empty">No partner organizations yet. Create one to get started.</div>';
    list.querySelectorAll('[data-school]').forEach((button) => button.addEventListener('click', () => select(Number(button.dataset.school))));
    const districts = state.schools.filter((school) => school.kind === 'district');
    $('orgParent').innerHTML = districts.map((school) => `<option value="${school.id}">${esc(school.name)}</option>`).join('');
  }
  function renderStats() {
    $('statSchools').textContent = state.schools.length;
    $('statDomains').textContent = state.schools.reduce((n, s) => n + s.domains.filter((d) => d.verified).length, 0);
    $('statStudents').textContent = state.schools.reduce((n, s) => n + s.members.students, 0);
    $('statTeachers').textContent = state.schools.reduce((n, s) => n + s.members.teachers, 0);
  }
  async function load() {
    const data = await api('/api/owner/schools');
    state.schools = data.schools;
    state.tiers = data.plan_tiers;
    if (!state.schools.some((s) => s.id === state.selected)) state.selected = state.schools[0]?.id ?? null;
    renderStats(); renderList();
    if (state.selected) await renderDetail();
    else $('detailCard').innerHTML = '<div class="owner-empty">Choose an institution to manage its plan, verify a school email domain, and review changes.</div>';
  }
  function selectedSchool() { return state.schools.find((s) => s.id === state.selected); }
  function select(id) { state.selected = id; state.proof = null; renderList(); renderDetail(); }
  async function renderDetail() {
    const school = selectedSchool();
    if (!school) return;
    const detail = $('detailCard');
    detail.innerHTML = `
      <div class="owner-head"><div><div class="owner-eyebrow">${esc(school.kind)}</div><h2 class="owner-section-title">${esc(school.name)}</h2><p class="owner-hint">${school.members.members} members · ${school.members.admins} school admins</p></div></div>
      <p class="owner-hint"><a href="/school-admin">Open school workspace controls</a> to assign school administrators, manage teacher access, and edit learning policies.</p>
      <h3 class="owner-section-title">Included school plan</h3><p class="owner-hint">This plan is controlled by the platform owner and applies to verified members of this institution.</p>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><select id="planTier" class="owner-select" style="max-width:220px">${state.tiers.map((tier) => `<option value="${tier}" ${tier === school.plan_tier ? 'selected' : ''}>${esc(tier)}</option>`).join('')}</select><button id="savePlan" class="owner-btn" type="button">Save plan</button></div>
      <p id="detailNotice" class="owner-notice" role="status"></p>
      <h3 class="owner-section-title" style="margin-top:20px">Verified school domains</h3><p class="owner-hint">Add a domain you control. Members are enrolled only after Google confirms the account email is verified.</p>
      <form id="domainForm"><label class="owner-label" for="domainName">Email domain</label><input id="domainName" class="owner-input" required placeholder="school.edu" autocomplete="off"><div class="owner-actions"><button class="owner-btn" type="submit">Add domain</button></div></form>
      <div id="proofBox"></div><div id="domainList">${school.domains.length ? school.domains.map((d) => `<div class="owner-domain"><div><div class="owner-domain-name">${esc(d.domain)}</div><small>${d.verified ? `Verified ${esc(new Date(d.verified_at).toLocaleDateString())}` : 'Verification required'}</small></div><div class="owner-actions" style="margin:0">${d.verified ? '<span class="owner-pill ok">Verified</span>' : `<button class="owner-btn secondary" type="button" data-verify="${d.id}">Check DNS</button>`}<button class="owner-btn danger" type="button" data-remove="${d.id}">Remove</button></div></div>`).join('') : '<div class="owner-empty">No domains added yet.</div>'}</div>
      <h3 class="owner-section-title" style="margin-top:24px">Recent changes</h3><div id="auditList" class="owner-audit"><div class="owner-hint">Loading audit history…</div></div>`;
    $('savePlan').addEventListener('click', async () => {
      const message = $('detailNotice');
      try { await api(`/api/owner/schools/${school.id}/plan`, { method: 'PUT', body: JSON.stringify({ plan_tier: $('planTier').value }) }); notice(message, 'School plan updated.', 'success'); await load(); }
      catch (error) { notice(message, error.message, 'error'); }
    });
    $('domainForm').addEventListener('submit', async (event) => {
      event.preventDefault(); const message = $('detailNotice');
      try {
        state.proof = await api(`/api/owner/schools/${school.id}/domains`, { method: 'POST', body: JSON.stringify({ domain: $('domainName').value }) });
        notice(message, 'Domain added. Publish this TXT record, then check DNS.', 'success');
        renderProof(); await load();
      } catch (error) { notice(message, error.message, 'error'); }
    });
    detail.querySelectorAll('[data-verify]').forEach((button) => button.addEventListener('click', async () => {
      try { await api(`/api/owner/schools/${school.id}/domains/${button.dataset.verify}/verify`, { method: 'POST', body: '{}' }); await load(); }
      catch (error) { notice($('detailNotice'), error.message, 'error'); }
    }));
    detail.querySelectorAll('[data-remove]').forEach((button) => button.addEventListener('click', async () => {
      if (!window.confirm('Remove this domain? Existing memberships remain, but no new accounts will be enrolled from it.')) return;
      try { await api(`/api/owner/schools/${school.id}/domains/${button.dataset.remove}`, { method: 'DELETE' }); await load(); }
      catch (error) { notice($('detailNotice'), error.message, 'error'); }
    }));
    renderProof();
    try {
      const audit = await api(`/api/owner/schools/${school.id}/audit`);
      const target = $('auditList');
      target.innerHTML = audit.events.length ? audit.events.map((event) => `<div class="owner-event"><span>${esc(event.action.replaceAll('_', ' '))}</span><small>${esc(event.created_at ? new Date(event.created_at).toLocaleString() : '')}</small></div>`).join('') : '<div class="owner-hint">No changes recorded yet.</div>';
    } catch (error) { $('auditList').textContent = error.message; }
  }
  function renderProof() {
    const target = $('proofBox');
    if (!target || !state.proof) return;
    target.innerHTML = `<div class="owner-proof"><strong>Publish this DNS TXT record</strong><p class="owner-hint" style="margin:6px 0">Add the record below at your DNS provider. The token is shown once, so keep this value until verified.</p><span>Name</span><code>${esc(state.proof.txt_name)}</code><span>Value</span><code>${esc(state.proof.txt_value)}</code></div>`;
  }
  $('orgKind').addEventListener('change', () => { $('parentWrap').hidden = $('orgKind').value !== 'school' || !state.schools.some((s) => s.kind === 'district'); });
  $('createOrgForm').addEventListener('submit', async (event) => {
    event.preventDefault(); const kind = $('orgKind').value;
    const parent = $('orgParent').value;
    const message = $('createNotice');
    try {
      await api('/api/owner/schools', { method: 'POST', body: JSON.stringify({ name: $('orgName').value, kind, parent_id: kind === 'school' && parent ? Number(parent) : null }) });
      $('orgName').value = ''; notice(message, 'Institution created.', 'success'); await load();
    } catch (error) { notice(message, error.message, 'error'); }
  });
  load().catch((error) => notice($('createNotice'), error.message, 'error'));
})();
