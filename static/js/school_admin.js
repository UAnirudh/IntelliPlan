'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const status = message => { $('admin-status').textContent = message; };
  let organizations = [], selected, dirty = false, saving = false;
  const policyViewport = window.matchMedia('(max-width: 520px)');
  const policyGroups = Array.from(document.querySelectorAll('.admin-policy-group[data-mobile-collapse]'));
  function syncPolicyDisclosure() {
    policyGroups.forEach(group => {
      if (!group.dataset.userToggled) group.open = !policyViewport.matches;
    });
  }
  policyGroups.forEach(group => group.querySelector('summary')?.addEventListener('click', () => {
    group.dataset.userToggled = '1';
  }));
  syncPolicyDisclosure();
  policyViewport.addEventListener?.('change', syncPolicyDisclosure);
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
    $('admin-stat-members').textContent = String(members.total);
    $('admin-stat-teachers').textContent = String(members.role_counts.teacher || 0);
    $('admin-stat-children').textContent = String(organizations.filter(o => o.parent_id === selected.id).length);
    const planNames = {free:'Free', pro:'Pro', premium:'Premium', premium_byok:'Premium with your AI key'};
    $('admin-stat-plan').textContent = planNames[selected.plan_tier] || selected.plan_tier || 'Free';
    $('admin-stat-plan-source').textContent = selected.plan_inherited
      ? `Inherited from ${selected.plan_source || 'the parent district'}`
      : selected.plan_tier === 'free'
        ? 'No paid school plan is assigned'
        : 'Assigned to this workspace by the platform owner';
    const addAdminRole = $('admin-role').querySelector('option[value="admin"]');
    if (members.can_manage_admin && !addAdminRole) {
      const option = document.createElement('option'); option.value = 'admin'; option.textContent = 'School administrator';
      $('admin-role').append(option);
    } else if (!members.can_manage_admin && addAdminRole) addAdminRole.remove();
    renderMembers(members);
    renderAudit(audit.events);
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
  function renderMembers(data) {
    const scope = selected;
    const container = $('admin-members');
    const generation = (renderMembers.generation || 0) + 1;
    renderMembers.generation = generation;
    container.replaceChildren();
    let loaded = 0;
    let loadButton = null;
    const appendMember = member => {
      const row = document.createElement('article'); row.className = 'admin-person-row';
      const identity = document.createElement('div'); identity.className = 'admin-person-identity';
      const name = document.createElement('strong'); name.textContent = member.name || member.email;
      const email = document.createElement('span'); email.textContent = `${member.email} · ID ${member.user_id}`;
      identity.append(name, email);
      const controls = document.createElement('div'); controls.className = 'admin-person-controls';
      const role = document.createElement('select'); role.setAttribute('aria-label', `Access role for ${member.email}`);
      const roleOptions = [['student','Student'],['teacher','Teacher']];
      if (data.can_manage_admin) roleOptions.push(['admin','School administrator']);
      for (const [value, label] of roleOptions) {
        const option = document.createElement('option'); option.value = value; option.textContent = label;
        role.append(option);
      }
      role.value = roleOptions.some(([value]) => value === member.role) ? member.role : 'student';
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove';
      remove.setAttribute('aria-label', `Remove ${member.email} from this workspace`);
      const lockedAdmin = member.role === 'admin' && !data.can_manage_admin;
      role.disabled = lockedAdmin;
      remove.disabled = lockedAdmin;
      if (lockedAdmin) {
        const note = document.createElement('small'); note.textContent = 'Admin access is managed by the platform owner.';
        controls.append(note);
      }
      role.addEventListener('change', async () => {
        if (saving) return;
        saving = true;
        $('admin-organization').disabled = true;
        role.disabled = true;
        try {
          await api(`/api/school-admin/organizations/${scope.id}/members`, {
            method:'POST', body:JSON.stringify({user_id:member.user_id, role:role.value}),
          });
          status(`${member.email} now has ${role.value} access.`);
          if (selected === scope) await render();
        } catch (error) { status(error.message); role.value = member.role; }
        finally { saving = false; role.disabled = lockedAdmin; $('admin-organization').disabled = !organizations.length; }
      });
      remove.addEventListener('click', async () => {
        if (saving || !window.confirm(`Remove ${member.email} from ${scope.name}?`)) return;
        saving = true;
        $('admin-organization').disabled = true;
        remove.disabled = true;
        try {
          await api(`/api/school-admin/organizations/${scope.id}/members`, {
            method:'DELETE', body:JSON.stringify({user_id:member.user_id}),
          });
          status(`${member.email} no longer has access to this workspace.`);
          if (selected === scope) await render();
        } catch (error) { status(error.message); remove.disabled = lockedAdmin; }
        finally { saving = false; $('admin-organization').disabled = !organizations.length; }
      });
      controls.append(role, remove); row.append(identity, controls);
      if (loadButton) container.insertBefore(row, loadButton);
      else container.append(row);
    };
    for (const member of data.members) appendMember(member);
    loaded = data.members.length;
    if (!data.total) {
      const empty = document.createElement('p'); empty.className = 'admin-empty';
      empty.textContent = 'No people have access to this workspace yet. Add a teacher by their school email to get started.';
      container.append(empty);
    }
    if (data.has_more) {
      loadButton = document.createElement('button');
      loadButton.type = 'button';
      loadButton.className = 'learning-secondary';
      loadButton.dataset.memberLoadMore = '';
      container.append(loadButton);
      const refreshLoadButton = () => {
        loadButton.textContent = `Load more people (${loaded} of ${data.total})`;
        loadButton.setAttribute('aria-label', `Load more people. ${loaded} of ${data.total} shown.`);
      };
      refreshLoadButton();
      loadButton.addEventListener('click', async () => {
        if (loadButton.disabled || selected !== scope) return;
        loadButton.disabled = true;
        loadButton.textContent = 'Loading people…';
        try {
          const next = await api(`/api/school-admin/organizations/${scope.id}/members?offset=${loaded}&limit=${data.limit}`);
          if (selected !== scope || renderMembers.generation !== generation) return;
          for (const member of next.members) appendMember(member);
          loaded += next.members.length;
          if (!next.has_more) loadButton.remove();
          else {
            loadButton.disabled = false;
            refreshLoadButton();
          }
        } catch (error) {
          if (selected === scope && renderMembers.generation === generation) {
            status(error.message);
            loadButton.disabled = false;
            loadButton.textContent = 'Try loading more people';
          }
        }
      });
    }
  }
  function renderAudit(events) {
    const container = $('admin-audit'); container.replaceChildren();
    const labels = {policy_updated:'Policy updated', membership_updated:'Member access changed',
      membership_removed:'Member access removed', organization_created:'Classroom created',
      beta_approval_updated:'Preview approval changed'};
    for (const event of events) {
      const item = document.createElement('article'); item.className = 'admin-audit-row';
      const heading = document.createElement('strong'); heading.textContent = labels[event.action] || event.action.replaceAll('_',' ');
      const byline = document.createElement('span');
      const actor = event.actor_name || event.actor_email || (event.actor_id ? `Account ${event.actor_id}` : 'Deleted account');
      const when = new Date(event.created_at);
      byline.textContent = `${actor} · ${Number.isNaN(when.getTime()) ? event.created_at : when.toLocaleString()}`;
      const details = document.createElement('small');
      const changes = event.changes || {};
      details.textContent = changes.email
        ? `${changes.email} · ${changes.role || 'access changed'}`
        : changes.user_id ? `Member ID ${changes.user_id}${changes.role ? ` · ${changes.role}` : ''}`
          : Object.keys(changes).length ? `${Object.keys(changes).length} setting${Object.keys(changes).length === 1 ? '' : 's'} updated` : 'Workspace change';
      item.append(heading, byline, details); container.append(item);
    }
    if (!events.length) container.textContent = 'Activity will appear here as admins make changes.';
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
    try { await api(`/api/school-admin/organizations/${target.id}/members`, {method:'POST', body:JSON.stringify({email:$('admin-member-email').value.trim(), role:$('admin-role').value})});
      $('admin-member-email').value = '';
      if (selected === target) { await render(); status('Workspace access updated.'); }
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
    const pageSize = 100;
    const organizationRows = [];
    let offset = 0;
    while (true) {
      const data = await api(`/api/school-admin/organizations?offset=${offset}&limit=${pageSize}`);
      organizationRows.push(...data.organizations);
      if (!data.has_more) break;
      const nextOffset = data.offset + data.organizations.length;
      if (!Number.isInteger(nextOffset) || nextOffset <= offset) {
        throw new Error('Could not load the complete organization list. Please reload.');
      }
      offset = nextOffset;
    }
    const selectedId = preferredId ?? selected?.id;
    organizations = organizationRows;
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
