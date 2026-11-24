(function () {
  'use strict';
  var list = document.getElementById('lacList');
  if (!list) return;
  var form = document.getElementById('lacAddForm');
  var button = document.getElementById('lacAddBtn');
  var message = document.getElementById('lacAddMsg');
  var quickMinutes = [15, 30, 45, 60];

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function hours(value) {
    var n = Math.max(0, Math.round(Number(value) || 0));
    return n < 60 ? n + ' min' : (Math.floor(n / 60) + (n % 60 ? 'h ' + (n % 60) + 'm' : 'h'));
  }
  function api(path, method, body) {
    return fetch(path, { method: method || 'GET', credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok || data.status === 'error') throw new Error(data.message || 'Could not save that. Try again.');
        return data;
      });
    });
  }
  function say(text, warn) {
    message.textContent = text || '';
    if (warn) message.dataset.tone = 'warn'; else message.removeAttribute('data-tone');
  }
  function render(data) {
    var rows = data && data.activities || [];
    window.dispatchEvent(new CustomEvent('ip-learning-data', { detail: { type: 'activities', items: rows } }));
    list.textContent = '';
    if (!rows.length) {
      list.appendChild(el('p', 'crs-empty', 'No classes or activities tracked yet. Add one here, or tell Plani what you want to make time for.'));
      return;
    }
    rows.forEach(function (activity) {
      var row = el('article', 'lac-card');
      row.dataset.state = activity.week.state;
      var info = el('div');
      info.appendChild(el('h3', 'lac-card__title', activity.title));
      var meta = el('div', 'lac-card__meta');
      var category = String(activity.category || 'activity').replace(/_/g, ' ');
      meta.appendChild(el('span', '', category.charAt(0).toUpperCase() + category.slice(1)));
      if (activity.weeks_in_a_row > 1) meta.appendChild(el('span', '', activity.weeks_in_a_row + ' weekly goals in a row'));
      if (activity.total_minutes > 0) meta.appendChild(el('span', '', hours(activity.total_minutes) + ' logged total'));
      if (activity.preferred_days && activity.preferred_days.length) {
        meta.appendChild(el('span', '', 'Prefers ' + activity.preferred_days.map(function (day) { return day.slice(0, 3); }).join(', ')));
      }
      if (activity.preferred_time) meta.appendChild(el('span', '', activity.preferred_time));
      info.appendChild(meta); row.appendChild(info);

      var count = el('div', 'lac-card__count');
      count.appendChild(el('strong', '', hours(activity.week.minutes)));
      count.appendChild(el('span', '', 'of ' + hours(activity.weekly_goal_minutes) + ' this week'));
      row.appendChild(count);

      var bar = el('div', 'lac-bar');
      bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-valuemin', '0');
      bar.setAttribute('aria-valuemax', '100'); bar.setAttribute('aria-valuenow', String(activity.week.percent));
      bar.setAttribute('aria-label', activity.title + ', weekly goal');
      var fill = el('i'); fill.style.setProperty('--lac-fill', String(activity.week.percent / 100));
      bar.appendChild(fill); row.appendChild(bar);
      row.appendChild(el('p', 'lac-card__status', activity.week.message));

      var actions = el('div', 'lac-card__actions');
      actions.appendChild(el('span', 'crs-log__label', 'Log time'));
      quickMinutes.forEach(function (mins) {
        var chip = el('button', 'lac-action', '+' + mins + ' min');
        chip.type = 'button'; chip.addEventListener('click', function () { checkin(activity, mins, actions); });
        actions.appendChild(chip);
      });
      var custom = el('input', 'form-input lac-custom'); custom.type = 'number'; custom.min = '1'; custom.max = '480'; custom.placeholder = 'Other';
      custom.setAttribute('aria-label', 'Minutes spent on ' + activity.title);
      custom.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' && custom.value) { event.preventDefault(); checkin(activity, Number(custom.value), actions); }
      });
      actions.appendChild(custom);
      var logCustom = el('button', 'lac-action lac-action--log', 'Log');
      logCustom.type = 'button'; logCustom.setAttribute('aria-label', 'Log custom minutes for ' + activity.title);
      logCustom.addEventListener('click', function () { checkin(activity, Number(custom.value), actions); });
      actions.appendChild(logCustom);
      var archive = el('button', 'lac-archive', 'Stop tracking'); archive.type = 'button';
      archive.addEventListener('click', function () {
        if (archive.dataset.armed !== '1') {
          archive.dataset.armed = '1'; archive.textContent = 'Press again to stop';
          setTimeout(function () { archive.dataset.armed = ''; archive.textContent = 'Stop tracking'; }, 4000);
          return;
        }
        archive.disabled = true;
        api('/api/activities/' + activity.id, 'DELETE').then(render).catch(function (err) { say(err.message, true); archive.disabled = false; });
      });
      actions.appendChild(archive); row.appendChild(actions); list.appendChild(row);
    });
  }
  function checkin(activity, mins, actions) {
    if (!Number.isFinite(mins) || mins <= 0 || mins > 480) { say('Enter a time between 1 and 480 minutes.', true); return; }
    Array.prototype.forEach.call(actions.querySelectorAll('button'), function (item) { item.disabled = true; });
    api('/api/activities/' + activity.id + '/checkin', 'POST', { minutes: mins })
      .then(function (data) { say('Progress saved.'); render(data); })
      .catch(function (err) { say(err.message, true); Array.prototype.forEach.call(actions.querySelectorAll('button'), function (item) { item.disabled = false; }); });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var title = document.getElementById('lacTitle').value.trim();
    if (!title) { say('Give this class or activity a name.', true); return; }
    button.disabled = true; button.textContent = 'Adding…'; say('');
    api('/api/activities', 'POST', { title: title,
      category: document.getElementById('lacCategory').value,
      weekly_goal_minutes: Number(document.getElementById('lacGoal').value),
      preferred_days: Array.prototype.map.call(document.querySelectorAll('.lac-days input:checked'), function (day) { return day.value; }),
      preferred_time: document.getElementById('lacPreferredTime').value
    }).then(function (data) { form.reset(); say('Added to your learning tracker and weekly plan.'); render(data); })
      .catch(function (err) { say(err.message, true); })
      .then(function () { button.disabled = false; button.textContent = 'Add'; });
  });

  function renderLoadError(err) {
    list.textContent = ''; list.appendChild(el('p', 'crs-empty', err.message));
    window.dispatchEvent(new CustomEvent('ip-learning-data', { detail: { type: 'activities', error: true } }));
    var retry = el('button', 'lac-retry', 'Retry'); retry.type = 'button';
    retry.addEventListener('click', function () {
      list.textContent = ''; var skeleton = el('div', 'lac-skeleton'); skeleton.appendChild(el('i')); list.appendChild(skeleton);
      api('/api/activities').then(render).catch(renderLoadError);
    });
    list.appendChild(retry);
  }
  api('/api/activities').then(render).catch(renderLoadError);
})();
