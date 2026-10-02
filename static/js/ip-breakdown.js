/* "Break it down" + "Just 5 minutes" — the step checklist on an assignment.
 *
 *   IPBreakdown.mount(container, assignment)
 *
 * `assignment` is the object the assignment modals already hold:
 * {title, course, due_date, id?, course_id?, estimated_time?}. The widget
 * shows saved steps if there are any, otherwise a "Break it down" button
 * with the granularity slider. Steps tick off in place; "Just 5 minutes"
 * starts an Active session on the first unfinished step and goes to /active.
 *
 * All text is set with textContent — step text can come from an LMS
 * description, which is untrusted.
 */
(function () {
  'use strict';

  var GRAN_LABELS = { 1: 'A few big steps', 2: 'Normal steps', 3: 'Tiny steps' };

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function tz() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { return ''; }
  }

  function api(method, path, body) {
    return fetch(path, {
      method: method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.message || 'Something went wrong.');
        return data;
      });
    });
  }

  function mins(m) {
    if (!m) return '';
    return m < 60 ? m + ' min' : Math.floor(m / 60) + 'h' + (m % 60 ? ' ' + (m % 60) + 'm' : '');
  }

  function Widget(container, assignment) {
    this.root = container;
    this.a = assignment || {};
    this.granularity = 2;
    this.data = null;
  }

  Widget.prototype.status = function (text, isError) {
    if (!this.statusEl) return;
    this.statusEl.textContent = text || '';
    this.statusEl.classList.toggle('is-error', !!isError);
  };

  Widget.prototype.load = function () {
    var self = this;
    this.render();
    return api('GET', '/api/breakdown?title=' + encodeURIComponent(this.a.title || ''))
      .then(function (data) { self.data = data; self.render(); })
      .catch(function () { self.data = { steps: [] }; self.render(); });
  };

  Widget.prototype.build = function () {
    var self = this;
    var description = '';
    var desc = document.getElementById('modalDescription');
    if (desc && desc.closest('#assignmentModal') && this.root.closest('#assignmentModal')) {
      description = desc.textContent || '';
      if (/^(Loading\.\.\.|No description available\.)$/.test(description.trim())) description = '';
    }
    this.status('Breaking it down…');
    this.setBusy(true);
    return api('POST', '/api/breakdown', {
      title: this.a.title,
      course: this.a.course || '',
      due_date: this.a.due_date || '',
      id: this.a.id || '',
      course_id: this.a.course_id || '',
      estimated_time: this.a.estimated_time || '',
      description: description,
      granularity: this.granularity,
      timezone: tz()
    }).then(function (data) {
      self.data = data;
      self.render();
      var where = data.placement && data.placement.label;
      var line = data.note || '';
      if (where) line = (line ? line + ' ' : '') + where + '.';
      else if (data.placement && data.placement.status === 'no_plan') {
        line = (line ? line + ' ' : '') + 'They will be scheduled when you build your plan.';
      }
      self.status(line);
    }).catch(function (err) {
      self.status(err.message, true);
    }).then(function () { self.setBusy(false); });
  };

  Widget.prototype.toggle = function (step, done) {
    var self = this;
    var body = { done: done };
    if (done && step.minutes >= 15) {
      // One optional number for steps big enough to be worth measuring: how
      // long it really took. Skipping it is fine — the Active timer's minutes
      // are used when it ran. Five-minute steps tick without a question.
      var answer = window.prompt('Done! Roughly how many minutes did “' + step.text + '” take? (optional)', '');
      var n = parseInt(answer, 10);
      if (n > 0) body.actual_minutes = n;
    }
    return api('PATCH', '/api/breakdown/steps/' + step.id, body)
      .then(function (data) { self.data = data; self.render(); })
      .catch(function (err) { self.status(err.message, true); });
  };

  Widget.prototype.startFive = function () {
    var self = this;
    this.setBusy(true);
    return api('POST', '/api/breakdown/start', { title: this.a.title })
      .then(function (data) { window.location.href = data.redirect || '/active'; })
      .catch(function (err) { self.status(err.message, true); self.setBusy(false); });
  };

  Widget.prototype.setBusy = function (busy) {
    Array.prototype.forEach.call(this.root.querySelectorAll('button, input'), function (b) {
      b.disabled = !!busy;
    });
  };

  Widget.prototype.render = function () {
    var self = this;
    var root = this.root;
    root.textContent = '';
    root.classList.add('ipb');

    var head = el('div', 'ipb__head');
    head.appendChild(el('div', 'ipb__label', 'Steps'));
    root.appendChild(head);

    var steps = (this.data && this.data.steps) || [];
    if (steps.length) {
      var done = this.data.done_count || 0;
      head.appendChild(el('span', 'ipb__count', done + ' of ' + steps.length + ' done · ' +
        mins(this.data.remaining_minutes) + ' left'));
      var list = el('ol', 'ipb__list');
      steps.forEach(function (step) {
        var li = el('li', 'ipb__step' + (step.done ? ' is-done' : ''));
        var label = el('label', 'ipb__check');
        var box = el('input');
        box.type = 'checkbox';
        box.checked = !!step.done;
        box.setAttribute('aria-label', 'Done: ' + step.text);
        box.addEventListener('change', function () { self.toggle(step, box.checked); });
        label.appendChild(box);
        label.appendChild(el('span', 'ipb__text', step.text));
        li.appendChild(label);
        li.appendChild(el('span', 'ipb__mins', mins(step.minutes)));
        if (step.evidence) {
          li.title = 'From the directions: “' + step.evidence + '”';
          li.appendChild(el('span', 'ipb__src', 'from the directions'));
        }
        list.appendChild(li);
      });
      root.appendChild(list);

      var next = this.data.next_step;
      if (next) {
        var five = el('button', 'btn-primary ipb__five', 'Just 5 minutes');
        five.type = 'button';
        five.title = 'Start a 5-minute focus session on: ' + next.text;
        five.addEventListener('click', function () { self.startFive(); });
        var nextLine = el('div', 'ipb__next');
        nextLine.appendChild(el('span', null, 'Next: '));
        nextLine.appendChild(el('strong', null, next.text));
        root.appendChild(nextLine);
        root.appendChild(five);
      }
      var cal = this.data.calibration;
      if (cal && cal.source !== 'none' && Math.abs(cal.ratio - 1) >= 0.05) {
        root.appendChild(el('div', 'ipb__hint',
          'Times adjusted to your pace (' + Math.round(cal.ratio * 100) + '% of the usual estimate, from ' +
          cal.samples + ' finished ' + (cal.source === 'steps' ? 'steps' : 'tasks') + ').'));
      }
    } else {
      root.appendChild(el('p', 'ipb__hint',
        'Stuck on where to start? Get a short list of concrete steps from this assignment\'s directions, ' +
        'each with a time, slotted into your plan.'));
    }

    var controls = el('div', 'ipb__controls');
    var sliderWrap = el('label', 'ipb__slider');
    var slider = el('input');
    slider.type = 'range'; slider.min = '1'; slider.max = '3'; slider.step = '1';
    slider.value = String(this.granularity);
    slider.setAttribute('aria-label', 'Step size');
    var sliderText = el('span', 'ipb__slider-text', GRAN_LABELS[this.granularity]);
    slider.addEventListener('input', function () {
      self.granularity = parseInt(slider.value, 10) || 2;
      sliderText.textContent = GRAN_LABELS[self.granularity];
    });
    sliderWrap.appendChild(slider);
    sliderWrap.appendChild(sliderText);
    var build = el('button', steps.length ? 'btn-secondary ipb__build' : 'btn-primary ipb__build',
      steps.length ? 'Redo steps' : 'Break it down');
    build.type = 'button';
    build.addEventListener('click', function () { self.build(); });
    controls.appendChild(sliderWrap);
    controls.appendChild(build);
    root.appendChild(controls);

    this.statusEl = el('div', 'ipb__status');
    this.statusEl.setAttribute('role', 'status');
    this.statusEl.setAttribute('aria-live', 'polite');
    root.appendChild(this.statusEl);
  };

  window.IPBreakdown = {
    mount: function (container, assignment) {
      if (!container || !assignment || !assignment.title) return null;
      var widget = new Widget(container, assignment);
      widget.load();
      return widget;
    }
  };
})();
