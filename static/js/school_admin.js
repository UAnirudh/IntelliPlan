'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const status = message => { $('admin-status').textContent = message; };
  let organizations = [], selected, dirty = false, saving = false;
  const settings = {tutor:'AI tutor', study:'Study sessions', review:'Review & flashcards', focus:'Focus tools',
    scheduler:'Scheduling', writing:'Writing assistant', math:'Math assistant', extractor:'Task extractor',
    text_dissector:'Text dissector', grade_modeler:'Grade modeler', meetings:'Meetings', groups:'Study groups',
    memories:'Memories & archives', pet:'Study pet', balance:'Balance tools', command_center:'Command Center',
    canvas:'Canvas connection', studentvue:'StudentVUE connection', calendar:'Calendar connection',
    schoology:'Schoology connection', hac:'Home Access Center connection', google_classroom:'Google Classroom connection',
    blackboard:'Blackboard connection', moodle:'Moodle connection', notion:'Notion connection', powerschool:'PowerSchool connection',
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
    const copy = document.createElement('span'); copy.className = 'policy-tool-copy';
    const title = document.createElement('span'); title.textContent = label;
    const hint = document.createElement('small'); hint.dataset.policyHint = '';
    copy.append(title, hint); wrapper.append(input, copy); wrapper.dataset.policySearch = label.toLowerCase();
    $(container).append(wrapper);
  }
  const connections = new Set(['canvas', 'studentvue', 'schoology', 'hac', 'google_classroom',
    'blackboard', 'moodle', 'notion', 'powerschool', 'calendar', 'uploads', 'notifications']);
  const experiments = new Set(['course_generator', 'generated_content', 'video_generation', 'practice_preview', 'allow_beta', 'third_party_processing']);
  const tools = new Set(['writing', 'math', 'extractor', 'text_dissector', 'grade_modeler', 'meetings',
    'groups', 'memories', 'pet', 'balance', 'command_center']);
  for (const [key, label] of Object.entries(settings)) {
    checkbox(connections.has(key) ? 'admin-connections' : experiments.has(key) ? 'admin-experiments' : tools.has(key) ? 'admin-tools' : 'admin-features', key, label);
  }
  for (const provider of ['groq', 'gemini', 'claude']) checkbox('admin-providers', provider, provider);
  async function render() {
    selected = organizations.find(o => o.id === Number($('admin-organization').value));
    if (!selected) { status('No organizations are assigned. The platform owner must provision a school or district.'); return; }
    const renderedOrganization = selected;
    dirty = false;
    const inherited = new Set(selected.inherited_restrictions || []);
    $('admin-save').disabled = false;
    $('admin-scope').textContent = `${selected.kind} · policy version ${selected.version}${selected.parent_id ? ' · parent institution restrictions also apply' : ''}`;
    $('admin-classroom-create').disabled = selected.kind !== 'school';
    $('admin-classroom-scope').textContent = selected.kind === 'school'
      ? 'Create a classroom in ' + selected.name + '. School and district restrictions will apply.'
      : selected.kind === 'class'
        ? 'This classroom is selected. Assign its teacher and students in Members below.'
        : 'Select a school above to create a classroom. Its school and district restrictions will apply.';
    for (const key of Object.keys(settings)) {
      const input = $(`policy-${key}`);
      input.checked = inherited.has(key) ? false : selected.policy[key] ?? key !== 'assessment_protection';
      input.disabled = inherited.has(key);
      input.closest('label').querySelector('[data-policy-hint]').textContent = inherited.has(key) ? 'Restricted by parent institution' : '';
    }
    updateSummary();
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
      if (!rows.length) { const p = document.createElement('p'); p.textContent = id === 'admin-members' ? 'No members added to this organization yet.' : 'Policy changes will appear here.'; $(id).append(p); }
    }
    const [beta, operations] = await Promise.all([api(`/api/school-admin/organizations/${renderedOrganization.id}/beta`), api(`/api/school-admin/organizations/${renderedOrganization.id}/operations`)]);
    if (selected !== renderedOrganization) return;
    $('admin-beta-participants').replaceChildren();
    for (const row of beta.participants) { const p = document.createElement('p'); p.textContent = `Account ${row.user_id} · ${row.approved ? 'approved' : 'revoked'} · ${row.accepted ? 'accepted notice' : 'not enrolled'}`; $('admin-beta-participants').append(p); }
    if (!beta.participants.length) $('admin-beta-participants').textContent = 'No preview participants approved yet.';
    $('admin-operations').replaceChildren();
    for (const row of operations.budgets) { const p = document.createElement('p'); p.textContent = `${row.month} · ${row.reserved_tokens} tokens reserved`; $('admin-operations').append(p); }
    for (const row of operations.feedback) { const p = document.createElement('p'); p.textContent = `${row.feature} · ${row.category}: ${row.message}`; $('admin-operations').append(p); }
    if (!operations.budgets.length && !operations.feedback.length) $('admin-operations').textContent = 'Generation reservations and participant feedback will appear here.';
  }
  function updateSummary() {
    const inputs = Object.keys(settings).map(key => $(`policy-${key}`));
    const off = inputs.filter(input => !input.checked && input.id !== 'policy-assessment_protection').length;
    $('admin-tool-summary').textContent = `${off} tool or processing setting${off === 1 ? '' : 's'} restricted${dirty ? ' · Unsaved changes' : ''}.`;
  }
  $('admin-policy').addEventListener('input', () => { dirty = true; updateSummary(); });
  $('admin-tool-filter').addEventListener('input', () => {
    const query = $('admin-tool-filter').value.trim().toLowerCase();
    const rows = [...document.querySelectorAll('[data-policy-search]')];
    rows.forEach(row => { row.hidden = !row.dataset.policySearch.includes(query); });
    $('admin-filter-empty').hidden = rows.some(row => !row.hidden);
  });
  $('admin-organization').addEventListener('change', () => {
    if (dirty || saving) {
      $('admin-organization').value = selected.id;
      status(saving ? 'Wait for the current policy to finish saving.' : 'Save your changes or reload the policy before choosing another organization.');
      return;
    }
    render().then(() => status('Policy loaded.')).catch(e => status(e.message));
  });
  $('admin-policy').addEventListener('submit', async event => {
    event.preventDefault(); if (!selected || saving) return; event.submitter.disabled = true; saving = true;
    $('admin-policy').inert = true;
    $('admin-organization').disabled = true;
    const targetOrganization = selected;
    try {
      const policy = {...targetOrganization.policy};
      for (const key of Object.keys(settings)) if (!$(`policy-${key}`).disabled) policy[key] = $(`policy-${key}`).checked;
      policy.providers = ['groq','gemini','claude'].filter(p => $(`policy-${p}`).checked);
      policy.max_generation_tokens = Number($('admin-budget').value);
      policy.monthly_generation_tokens = Number($('admin-monthly-budget').value);
      policy.retention_days = Number($('admin-retention').value);
      policy.assessment_windows = $('admin-assessment-windows').value.trim() ? $('admin-assessment-windows').value.trim().split('\n').map(line => { const pair = line.split(' / '); return {start: pair[0]?.trim(), end: pair[1]?.trim()}; }) : [];
      const data = await api(`/api/school-admin/organizations/${targetOrganization.id}/policy`, {method:'PUT', body:JSON.stringify({policy, version:targetOrganization.version})});
      targetOrganization.policy = policy; targetOrganization.version = data.version;
      if (selected === targetOrganization) { await loadOrganizations(); status('Policy saved. New requests use the current restrictions.'); }
    } catch (e) { status(e.message); } finally {
      event.submitter.disabled = false; saving = false;
      $('admin-policy').inert = false;
      $('admin-organization').disabled = !organizations.length;
    }
  });
  $('admin-member').addEventListener('submit', async event => {
    event.preventDefault(); if (!selected || saving) return; event.submitter.disabled = true;
    const target = selected; saving = true; $('admin-organization').disabled = true;
    try { await api(`/api/school-admin/organizations/${target.id}/members`, {method:'POST', body:JSON.stringify({user_id:Number($('admin-user-id').value), role:$('admin-role').value})});
      if (selected === target) { await render(); status('Membership saved.'); }
    } catch(e) { status(e.message); } finally {
      saving = false; event.submitter.disabled = false;
      $('admin-organization').disabled = !organizations.length;
    }
  });
  $('admin-classroom').addEventListener('submit', async event => {
    event.preventDefault();
    if (!selected || selected.kind !== 'school' || saving) return;
    if (dirty) { status('Save your policy changes or reload before creating a classroom.'); return; }
    const school = selected;
    saving = true;
    $('admin-classroom-create').disabled = true;
    $('admin-organization').disabled = true;
    $('admin-member').inert = true;
    $('admin-policy').inert = true;
    try {
      const data = await api('/api/school-admin/organizations', {method:'POST', body:JSON.stringify({
        kind:'class', parent_id:school.id, name:$('admin-classroom-name').value.trim(), policy:{}
      })});
      $('admin-classroom-name').value = '';
      await loadOrganizations(data.id);
      status('Classroom created. Assign its teacher and students in Members.');
    } catch(error) { status(error.message); }
    finally {
      saving = false;
      $('admin-organization').disabled = !organizations.length;
      $('admin-classroom-create').disabled = selected?.kind !== 'school';
      $('admin-member').inert = false;
      $('admin-policy').inert = false;
    }
  });
  $('admin-beta').addEventListener('submit', async event => {
    event.preventDefault(); if (!selected) return;
    const target = selected; event.submitter.disabled = true;
    try {
      await api(`/api/school-admin/organizations/${target.id}/beta`, {method:'POST', body:JSON.stringify({feature:'practice_preview', user_id:Number($('admin-beta-user').value), approved:$('admin-beta-approved').value === 'true'})});
      if (selected === target) { await render(); status('Preview approval updated. The member chooses whether to enroll.'); }
    } catch(error) { status(error.message); } finally { event.submitter.disabled = false; }
  });
  async function loadOrganizations(preferredId) {
    const data = await api('/api/school-admin/organizations');
    const selectedId = preferredId ?? selected?.id;
    organizations = data.organizations;
    $('admin-organization').replaceChildren();
    for (const org of organizations) { const option = document.createElement('option'); option.value = org.id; option.textContent = org.name; $('admin-organization').append(option); }
    $('admin-organization').disabled = !organizations.length;
    if (organizations.some(org => org.id === selectedId)) $('admin-organization').value = selectedId;
    await render();
  }
  $('admin-reload').addEventListener('click', () => {
    if (!saving) loadOrganizations().then(() => status('Latest policy loaded.')).catch(e => status(e.message));
  });
  loadOrganizations().catch(e => status(e.message));
})();
