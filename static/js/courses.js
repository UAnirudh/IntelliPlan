/* The Courses page: list tracked courses, add one, log time, find one.
 *
 * Wrapped in a function and prefixed crs- throughout, because base.html
 * defines a lot of globals and a collision stops this whole script.
 * Every course field is written with textContent; nothing a student typed
 * is ever parsed as HTML.
 */
(function () {
  'use strict';

  var list = document.getElementById('crsList');
  if (!list) return;

  var addForm = document.getElementById('crsAddForm');
  var addBtn = document.getElementById('crsAddBtn');
  var addMsg = document.getElementById('crsAddMsg');
  var findForm = document.getElementById('crsFindForm');
  var findResults = document.getElementById('crsFindResults');
  var QUICK_MINUTES = [15, 30, 45, 60];

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function hours(mins) {
    var n = Math.max(0, Math.round(Number(mins) || 0));
    var h = Math.floor(n / 60), m = n % 60;
    if (!h) return m + ' min';
    return m ? h + 'h ' + m + 'm' : h + 'h';
  }

  function api(path, method, body) {
    return fetch(path, {
      method: method || 'GET',
      credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok || data.status === 'error') {
          throw new Error(data.message || 'That did not work. Try again.');
        }
        return data;
      });
    });
  }

  function say(node, text, warn) {
    node.textContent = text || '';
    if (warn) node.dataset.tone = 'warn'; else node.removeAttribute('data-tone');
  }

  // ── One course row ─────────────────────────────────────────────────

  function courseRow(course) {
    var row = el('article', 'crs-course');
    row.dataset.state = course.week.state;

    var head = el('div');
    var title = el('h3', 'crs-course__title');
    var link = el('a', null, course.title);
    link.href = course.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    title.appendChild(link);
    head.appendChild(title);

    var meta = el('div', 'crs-course__meta');
    meta.appendChild(el('span', null, course.provider_name));
    if (course.weeks_in_a_row > 1) {
      meta.appendChild(el('span', null, course.weeks_in_a_row + ' weeks in a row'));
    }
    if (course.total_minutes > 0) {
      meta.appendChild(el('span', null, hours(course.total_minutes) + ' logged in total'));
    }
    if (course.verified_percent != null) {
      meta.appendChild(el('strong', null, Math.round(course.verified_percent) + '% complete, verified'));
    }
    head.appendChild(meta);
    row.appendChild(head);

    var figure = el('div', 'crs-course__figure');
    figure.appendChild(el('b', null, hours(course.week.minutes)));
    figure.appendChild(el('span', null, 'of ' + hours(course.weekly_goal_minutes) + ' this week'));
    row.appendChild(figure);

    var bar = el('div', 'crs-bar');
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-valuemin', '0');
    bar.setAttribute('aria-valuemax', '100');
    bar.setAttribute('aria-valuenow', String(course.week.percent));
    bar.setAttribute('aria-label', course.title + ', this week');
    var fill = el('i');
    fill.style.setProperty('--crs-fill', String(course.week.percent / 100));
    bar.appendChild(fill);
    row.appendChild(bar);

    row.appendChild(el('p', 'crs-course__status', course.week.message));

    var log = el('div', 'crs-log');
    log.appendChild(el('span', 'crs-log__label', 'Log time'));
    QUICK_MINUTES.forEach(function (mins) {
      var chip = el('button', 'crs-chip', '+' + mins + ' min');
      chip.type = 'button';
      chip.addEventListener('click', function () { checkin(course.id, mins, log); });
      log.appendChild(chip);
    });
    var custom = el('input', 'form-input crs-log__custom');
    custom.type = 'number'; custom.min = '1'; custom.max = '480'; custom.placeholder = 'Other';
    custom.setAttribute('aria-label', 'Minutes spent on ' + course.title);
    custom.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if (custom.value) checkin(course.id, Number(custom.value), log);
    });
    log.appendChild(custom);

    var remove = el('button', 'crs-remove', 'Stop tracking');
    remove.type = 'button';
    remove.addEventListener('click', function () {
      if (remove.dataset.armed !== '1') {
        // Two presses, not a dialog: the first says what the second does.
        remove.dataset.armed = '1';
        remove.textContent = 'Press again to stop tracking';
        setTimeout(function () {
          remove.dataset.armed = '';
          remove.textContent = 'Stop tracking';
        }, 4000);
        return;
      }
      api('/api/courses/' + course.id, 'DELETE').then(render).catch(function (err) {
        say(addMsg, err.message, true);
      });
    });
    log.appendChild(remove);
    row.appendChild(log);
    return row;
  }

  function checkin(courseId, minutes, log) {
    var buttons = log.querySelectorAll('button');
    buttons.forEach(function (b) { b.disabled = true; });
    api('/api/courses/' + courseId + '/checkin', 'POST', { minutes: minutes })
      .then(render)
      .catch(function (err) {
        buttons.forEach(function (b) { b.disabled = false; });
        say(addMsg, err.message, true);
      });
  }

  function render(data) {
    var courses = (data && data.courses) || [];
    list.textContent = '';
    if (!courses.length) {
      list.appendChild(el('p', 'crs-empty',
        'Nothing tracked yet. Paste a course link above and pick how much time you want to give it each week.'));
      return;
    }
    courses.forEach(function (course) { list.appendChild(courseRow(course)); });
  }

  // ── Add ────────────────────────────────────────────────────────────

  addForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var url = document.getElementById('crsUrl').value.trim();
    if (!url) { say(addMsg, 'Paste the link to the course first.', true); return; }
    addBtn.disabled = true;
    say(addMsg, '');
    api('/api/courses', 'POST', {
      url: url,
      title: document.getElementById('crsTitle').value,
      weekly_goal_minutes: Number(document.getElementById('crsGoal').value)
    }).then(function (data) {
      addForm.reset();
      say(addMsg, 'Tracking it. This week’s time is on your plan.');
      render(data);
    }).catch(function (err) {
      say(addMsg, err.message, true);
    }).then(function () { addBtn.disabled = false; });
  });

  // ── Find ───────────────────────────────────────────────────────────

  findForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var subject = document.getElementById('crsSubject').value.trim();
    findResults.textContent = '';
    if (!subject) return;
    api('/api/courses/recommend?subject=' + encodeURIComponent(subject)).then(function (data) {
      (data.results || []).forEach(function (r) {
        if (!/^https:\/\//i.test(r.url)) return;
        var a = el('a', 'crs-result');
        a.href = r.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        a.appendChild(el('b', null, 'Search ' + r.name + ' for “' + subject + '”'));
        a.appendChild(el('span', null, r.why));
        findResults.appendChild(a);
      });
    }).catch(function (err) {
      findResults.appendChild(el('p', 'crs-empty', err.message));
    });
  });

  api('/api/courses').then(render).catch(function (err) {
    list.textContent = '';
    list.appendChild(el('p', 'crs-empty', err.message));
  });
})();
