(function () {
  'use strict';
  var datasets = { courses: null, activities: null };
  var ids = {
    minutes: document.getElementById('learningMetricMinutes'),
    goal: document.getElementById('learningMetricGoal'),
    done: document.getElementById('learningMetricDone'),
    streak: document.getElementById('learningMetricStreak'),
    percent: document.getElementById('learningMetricPercent'),
    track: document.getElementById('learningProgressTrack'),
    fill: document.getElementById('learningProgressFill'),
    status: document.getElementById('learningOverviewStatus')
  };
  if (!ids.minutes) return;
  function duration(minutes) {
    var n = Math.max(0, Math.round(Number(minutes) || 0));
    if (n < 60) return n + ' min';
    return Math.floor(n / 60) + 'h' + (n % 60 ? ' ' + (n % 60) + 'm' : '');
  }
  function update() {
    if (datasets.courses === false || datasets.activities === false) {
      ids.status.textContent = 'Some progress could not load. Retry below.';
      ids.minutes.textContent = ids.goal.textContent = ids.done.textContent = ids.streak.textContent = ids.percent.textContent = '—';
      ids.track.setAttribute('aria-valuenow', '0'); ids.fill.style.setProperty('--lac-total-fill', '0');
      return;
    }
    if (!Array.isArray(datasets.courses) || !Array.isArray(datasets.activities)) return;
    var all = datasets.courses.concat(datasets.activities);
    var minutes = 0, goal = 0, done = 0, longest = 0;
    all.forEach(function (item) {
      var week = item.week || {};
      minutes += Number(week.minutes) || 0;
      goal += Number(item.weekly_goal_minutes) || 0;
      if (week.state === 'done' || Number(week.percent) >= 100) done += 1;
      longest = Math.max(longest, Number(item.weeks_in_a_row) || 0);
    });
    ids.minutes.textContent = duration(minutes);
    ids.goal.textContent = duration(goal);
    ids.done.textContent = String(done);
    ids.streak.textContent = String(longest);
    var percent = goal ? Math.min(100, Math.round(minutes * 100 / goal)) : 0;
    ids.percent.textContent = percent + '%';
    ids.track.setAttribute('aria-valuenow', String(percent));
    ids.fill.style.setProperty('--lac-total-fill', String(percent / 100));
    ids.status.textContent = all.length ? all.length + (all.length === 1 ? ' learning goal' : ' learning goals') : 'Ready when you are';
  }
  window.addEventListener('ip-learning-data', function (event) {
    var detail = event.detail || {};
    if (detail.type !== 'courses' && detail.type !== 'activities') return;
    datasets[detail.type] = detail.error ? false : (Array.isArray(detail.items) ? detail.items : []);
    update();
  });
})();
