'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const status = message => { $('admin-status').textContent = message; };
  let organizations = [], selected;
  const settings = {tutor:'AI tutor', study:'Study sessions', review:'Review & flashcards', focus:'Focus tools',
    scheduler:'Scheduling', canvas:'Canvas connection', studentvue:'StudentVUE connection', calendar:'Calendar connection',
    uploads:'File uploads', notifications:'Notifications', course_generator:'Course generator · Admin Beta',
    generated_content:'Original course generation · Admin Beta', video_generation:'Video generation · unavailable alpha',
    practice_preview:'Reviewed practice preview · Opt-in Beta',
    allow_beta:'Allow eligible beta features', third_party_processing:'Allow third-party AI processing',
    assessment_protection:'Enable strict assessment protection'};
  async function api(path, options = {}) {
    const r = await fetch(path, {credentials:'same-origin', ...options, headers:{'Content-Type':'application/json'}});
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.message || data.error || `Request failed (${r.status}).`);
    return data;
  }
  function checkbox(container, key, label) {
    const wrapper = document.createElement('label'); wrapper.className = 'learning-consent';
    const input = document.createElement('input'); input.type = 'checkbox'; input.id = `policy-${key}`;
    wrapper.append(input, document.createTextNode(label)); $(container).append(wrapper);
  }
  for (const [key, label] of Object.entries(settings)) checkbox('admin-features', key, label);
  for (const provider of ['groq', 'gemini', 'claude']) checkbox('admin-providers', provider, provider);
  async function render() {
    selected = organizations.find(o => o.id === Number($('admin-organization').value));
    if (!selected) { status('No organizations are assigned. The platform owner must provision a school or district.'); return; }
    const renderedOrganization = selected;
    $('admin-scope').textContent = `${selected.kind} · policy version ${selected.version}${selected.parent_id ? ' · parent district restrictions also apply' : ''}`;
    for (const key of Object.keys(settings)) $(`policy-${key}`).checked = selected.policy[key] ?? key !== 'assessment_protection';
    for (const p of ['groq','gemini','claude']) $(`policy-${p}`).checked = (selected.policy.providers || ['groq','gemini','claude']).includes(p);
    $('admin-budget').value = selected.policy.max_generation_tokens || 24000;
    $('admin-monthly-budget').value = selected.policy.monthly_generation_tokens || 100000;
    $('admin-retention').value = selected.policy.retention_days || 0;
    $('admin-assessment-windows').value = (selected.policy.assessment_windows || []).map(w => `${w.start} / ${w.end}`).join('\n');
    const [members, audit] = await Promise.all([api(`/api/school-admin/organizations/${selected.id}/members`), api(`/api/school-admin/organizations/${selected.id}/audit`)]);
    if (selected !== renderedOrganization) return;
    for (const [id, rows, format] of [['admin-members', members.members, r => `Account ${r.user_id} · ${r.role}`],
      ['admin-audit', audit.events, r => `${r.created_at} · ${r.action} · actor ${r.actor_id || 'deleted account'}`]]) {
      $(id).replaceChildren(); for (const row of rows) { const p = document.createElement('p'); p.textContent = format(row); $(id).append(p); }
    }
    const [beta, operations] = await Promise.all([api(`/api/school-admin/organizations/${renderedOrganization.id}/beta`), api(`/api/school-admin/organizations/${renderedOrganization.id}/operations`)]);
    if (selected !== renderedOrganization) return;
    $('admin-beta-participants').replaceChildren();
    for (const row of beta.participants) { const p = document.createElement('p'); p.textContent = `Account ${row.user_id} · ${row.approved ? 'approved' : 'revoked'} · ${row.accepted ? 'accepted notice' : 'not enrolled'}`; $('admin-beta-participants').append(p); }
    $('admin-operations').replaceChildren();
    for (const row of operations.budgets) { const p = document.createElement('p'); p.textContent = `${row.month} · ${row.reserved_tokens} tokens reserved`; $('admin-operations').append(p); }
    for (const row of operations.feedback) { const p = document.createElement('p'); p.textContent = `${row.feature} · ${row.category}: ${row.message}`; $('admin-operations').append(p); }
  }
  $('admin-organization').addEventListener('change', () => render().catch(e => status(e.message)));
  $('admin-policy').addEventListener('submit', async event => {
    event.preventDefault(); if (!selected) return; event.submitter.disabled = true;
    const targetOrganization = selected;
    try {
      const policy = Object.fromEntries(Object.keys(settings).map(k => [k, $(`policy-${k}`).checked]));
      policy.providers = ['groq','gemini','claude'].filter(p => $(`policy-${p}`).checked);
      policy.max_generation_tokens = Number($('admin-budget').value);
      policy.monthly_generation_tokens = Number($('admin-monthly-budget').value);
      policy.retention_days = Number($('admin-retention').value);
      policy.assessment_windows = $('admin-assessment-windows').value.trim() ? $('admin-assessment-windows').value.trim().split('\n').map(line => { const pair = line.split(' / '); return {start: pair[0]?.trim(), end: pair[1]?.trim()}; }) : [];
      const data = await api(`/api/school-admin/organizations/${targetOrganization.id}/policy`, {method:'PUT', body:JSON.stringify({policy, version:targetOrganization.version})});
      targetOrganization.policy = policy; targetOrganization.version = data.version;
      if (selected === targetOrganization) { await render(); status('Policy saved. New requests use the current restrictions.'); }
    } catch (e) { status(e.message); } finally { event.submitter.disabled = false; }
  });
  $('admin-member').addEventListener('submit', async event => {
    event.preventDefault(); if (!selected) return; event.submitter.disabled = true;
    try { await api(`/api/school-admin/organizations/${selected.id}/members`, {method:'POST', body:JSON.stringify({user_id:Number($('admin-user-id').value), role:$('admin-role').value})});
      await render(); status('Membership saved.');
    } catch(e) { status(e.message); } finally { event.submitter.disabled = false; }
  });
  $('admin-beta').addEventListener('submit', async event => {
    event.preventDefault(); if (!selected) return;
    const target = selected; event.submitter.disabled = true;
    try {
      await api(`/api/school-admin/organizations/${target.id}/beta`, {method:'POST', body:JSON.stringify({feature:'practice_preview', user_id:Number($('admin-beta-user').value), approved:$('admin-beta-approved').value === 'true'})});
      if (selected === target) { await render(); status('Preview approval updated. The member chooses whether to enroll.'); }
    } catch(error) { status(error.message); } finally { event.submitter.disabled = false; }
  });
  api('/api/school-admin/organizations').then(async data => {
    organizations = data.organizations;
    for (const org of organizations) { const option = document.createElement('option'); option.value = org.id; option.textContent = org.name; $('admin-organization').append(option); }
    await render();
  }).catch(e => status(e.message));
})();
