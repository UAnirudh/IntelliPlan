(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const dialog = el('educationPlanDialog');
  let savedPlan = null;
  let busy = false;
  const status = text => { el('educationPlanStatus').textContent = text; };
  const text = (tag, value, parent) => {
    const node = document.createElement(tag);
    node.textContent = value;
    parent.append(node);
    return node;
  };
  async function api(path = '', method = 'GET', body) {
    const response = await fetch(`/api/tutor/adaptive/education-plan${path}`, {
      method, headers: { 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not update your plan.');
    return data.plan;
  }
  function render(plan, fillForm = true) {
    savedPlan = plan;
    const known = el('educationKnown');
    const milestones = el('educationMilestones');
    known.replaceChildren(); milestones.replaceChildren();
    known.hidden = milestones.hidden = !plan;
    el('educationDelete').hidden = false;
    el('educationBuild').textContent = plan ? 'Refresh school data and rebuild plan' : 'Build my learning plan';
    if (!plan) return;
    if (fillForm) {
      el('educationSubject').value = plan.goal.subject;
      el('educationTarget').value = plan.goal.target;
      el('educationStarting').value = plan.goal.starting_point;
      el('educationMinutes').value = plan.goal.weekly_minutes;
      el('educationDate').value = plan.goal.target_date;
    }
    const snapshot = plan.snapshot;
    text('h3', 'What Plani can use', known);
    text('p', `School snapshot: ${new Date(snapshot.as_of).toLocaleString()}. Grade: ${snapshot.grade || 'not specified'}.`, known);
    text('p', `Courses: ${snapshot.courses.length ? snapshot.courses.map(c => `${c.course}${c.percent == null ? '' : ` (${c.percent}%)`}`).join(' · ') : 'No grade data available in this snapshot.'}`, known);
    text('p', `Available assignments: ${snapshot.assignments.length}. Checked skills at build time: ${snapshot.practice.length}. New scored checks update milestone evidence when you reload.`, known);
    for (const [source, state] of Object.entries(snapshot.sources)) {
      if (state !== 'available') text('p', `${source}: ${state.replaceAll('_', ' ')}.`, known);
    }
    const details = document.createElement('details');
    text('summary', 'View schoolwork and context limits', details);
    for (const item of snapshot.assignments) text('p', `${item.course} · ${item.title}${item.due_date ? ` · due ${item.due_date}` : ''}`, details);
    for (const limit of snapshot.limits) text('p', limit, details);
    known.append(details);
    text('h3', 'Your route to the goal', milestones);
    text('p', 'These are suggested milestones. Progress choices below are your reports; checked-answer evidence is shown separately.', milestones);
    if (!plan.next_step) text('p', 'You have reported every milestone complete. Ask Plani for an independent transfer check before setting your next goal.', milestones);
    for (const step of plan.steps) {
      const card = document.createElement('article');
      card.className = `education-step${plan.next_step?.id === step.id ? ' current' : ''}`;
      text('h3', `${step.id}. ${step.title}`, card);
      text('p', step.objective, card);
      text('p', `Show it independently: ${step.success_criteria}`, card);
      if (step.evidence) text('p', `Checked practice: ${step.evidence.independent_correct} independent correct of ${step.evidence.total_attempts} attempts on ${step.evidence.topic}.`, card);
      else text('p', 'No matching scored check yet. Begin with the diagnostic and explain your reasoning.', card);
      const actions = document.createElement('div'); actions.className = 'education-actions';
      const start = text('button', step.teaching_move === 'repair' ? 'Work through this with Plani' : 'Start this lesson', actions);
      start.type = 'button';
      start.addEventListener('click', () => {
        dialog.close();
        const wanted = /math|algebra|calculus|geometry/i.test(plan.goal.subject) ? 'Math' : /english|writing|reading|literature/i.test(plan.goal.subject) ? 'English' : plan.goal.subject;
        const chips = Array.from(document.querySelectorAll('#tutorChips .tutor-chip'));
        const chip = chips.find(c => c.dataset.subject.toLowerCase() === wanted.toLowerCase()) || chips.find(c => c.dataset.subject === 'General');
        if (chip) selectSubject(chip);
        const input = el('tutorInput');
        input.value = `Continue my learning plan toward ${plan.goal.target}. Let's work on ${step.title}. My reported progress is ${step.status.replaceAll('_', ' ')}. Start with one diagnostic question: ${step.diagnostic} Then respond to my reasoning one step at a time.`;
        autoResizeTutor(input);
        sendTutorMessage();
      });
      if (step.skill_id) {
        const check = text('button', 'Check this skill', actions);
        check.type = 'button';
        check.addEventListener('click', () => {
          dialog.close();
          const area = /m\d+$/.test(step.skill_id) ? 'math' : /r\d+$/.test(step.skill_id) ? 'reading' : 'writing';
          document.getElementById('tutorCheckArea').value = area;
          startTutorCheck(area, step.skill_id);
        });
      }
      card.append(actions);
      const label = text('label', 'My progress', card);
      const select = document.createElement('select');
      for (const [value, name] of [['not_started', 'Not started'], ['practicing', 'Practicing'], ['needs_help', 'I am stuck'], ['completed', 'I completed this (my report)']]) {
        const option = document.createElement('option'); option.value = value; option.textContent = name; select.append(option);
      }
      select.value = step.status; label.append(select);
      select.addEventListener('change', async () => {
        if (busy) { select.value = step.status; return; }
        busy = true; select.disabled = true;
        try {
          render(await api(`/steps/${step.id}`, 'PATCH', { status: select.value, revision: savedPlan.revision }), false);
          status('Progress saved. Plani will use this on your next lesson.');
        } catch (error) { select.value = step.status; status(error.message); }
        finally { busy = false; select.disabled = false; }
      });
      milestones.append(card);
    }
  }
  async function load() {
    if (busy) return;
    busy = true; status('Loading your goal and education plan…');
    try { render(await api()); status(savedPlan ? 'Plan ready. Choose a lesson or update your goal.' : 'Set a goal to build your first learning plan.'); }
    catch (error) { render(null); status(`${error.message} You can manage AI personalization in Settings.`); }
    finally { busy = false; }
  }
  el('educationPlanOpen').addEventListener('click', () => { dialog.showModal(); load(); });
  el('educationPlanClose').addEventListener('click', () => dialog.close());
  el('educationReload').addEventListener('click', load);
  el('educationPlanForm').addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    busy = true; el('educationBuild').disabled = true;
    status('Reading available school context and building your learning route…');
    try {
      const goal = { subject: el('educationSubject').value, target: el('educationTarget').value,
        starting_point: el('educationStarting').value, target_date: el('educationDate').value,
        weekly_minutes: Number(el('educationMinutes').value) };
      render(await api('', 'POST', { goal, revision: savedPlan?.revision || 0 }));
      status('Your plan is ready. Plani now uses this goal and context in your lessons.');
    } catch (error) { status(error.message); }
    finally { busy = false; el('educationBuild').disabled = false; }
  });
  el('educationDelete').addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    try { await api('', 'DELETE'); render(null); status('Plan and education snapshot deleted. Your other learning records are unchanged.'); }
    catch (error) { status(error.message); }
    finally { busy = false; }
  });
  if (new URLSearchParams(location.search).get('plan') === '1') { dialog.showModal(); load(); }
})();
