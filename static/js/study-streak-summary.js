(() => {
  'use strict';

  const card = document.querySelector('[data-study-streak]');
  const navCounts = ['sideStreakMeta', 'navStreakMeta'].map(id => document.getElementById(id)).filter(Boolean);
  const weekday = new Intl.DateTimeFormat(undefined, { weekday: 'short', timeZone: 'UTC' });

  function drawWeek(container, currentDate, history) {
    const today = new Date(currentDate + 'T00:00:00Z');
    const monday = today.getUTCDate() - (today.getUTCDay() + 6) % 7;
    const dates = [];
    for (let offset = 0; offset < 7; offset++) {
      const day = new Date(today);
      day.setUTCDate(monday + offset);
      const iso = day.toISOString().slice(0, 10);
      const name = weekday.format(day);
      const item = document.createElement('li');
      item.textContent = name;
      item.appendChild(document.createElement('i')).setAttribute('aria-hidden', 'true');
      item.setAttribute('aria-label', history.has(iso) ? name + ', counted' : name);
      item.toggleAttribute('data-complete', history.has(iso));
      if (iso === currentDate) item.setAttribute('aria-current', 'date');
      dates.push(item);
    }
    container.replaceChildren(...dates);
  }

  async function refresh() {
    try {
      const response = await fetch('/study/streak?summary=1', {
        credentials: 'same-origin',
        headers: { 'X-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone || '' }
      });
      if (!response.ok) return;
      const data = await response.json();
      if (data.status !== 'ok') return;
      const count = data.streak_count;
      const days = count === 1 ? '1 day' : count + ' days';
      for (const item of navCounts) {
        item.textContent = days;
        item.hidden = count < 1;
      }
      if (!card) return;
      const history = new Set(data.streak_history);
      card.querySelector('[data-streak-count]').textContent = count ? days + ' on a roll' : 'Start your study streak';
      card.querySelector('[data-streak-message]').textContent = history.has(data.today)
        ? 'Today is checked off. Best streak: ' + data.longest_streak + (data.longest_streak === 1 ? ' day' : ' days')
        : 'Finish 5 practice questions to mark today.';
      drawWeek(card.querySelector('[data-streak-week]'), data.today, history);
      card.hidden = false;
    } catch (_) {
      // A failed refresh leaves the last verified progress on screen.
    }
  }

  if (card || navCounts.length) {
    refresh();
    window.addEventListener('intelliplan:streak-updated', refresh);
    window.addEventListener('pageshow', event => { if (event.persisted) refresh(); });
  }
})();
