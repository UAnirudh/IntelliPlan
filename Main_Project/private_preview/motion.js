(() => {
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const bar = document.querySelector('.segments');
  const tabs = [...bar.querySelectorAll('[role="tab"]')];
  const panels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
  let selected = 0;
  let transition;

  function select(index, focus = false) {
    const previous = selected;
    selected = index;
    transition?.cancel();
    bar.style.setProperty('--active-tab', index);
    tabs.forEach((tab, position) => {
      tab.setAttribute('aria-selected', String(position === index));
      tab.tabIndex = position === index ? 0 : -1;
      panels[position].hidden = position !== index;
    });
    if (focus) tabs[index].focus();
    if (previous !== index && !reduceMotion.matches) {
      transition = panels[index].animate([
        { opacity: .4, transform: `translateX(${index > previous ? 8 : -8}px)` },
        { opacity: 1, transform: 'translateX(0)' },
      ], { duration: 210, easing: 'ease-out' });
    }
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => select(index));
    tab.addEventListener('keydown', event => {
      const destination = {
        ArrowLeft: (index + tabs.length - 1) % tabs.length,
        ArrowRight: (index + 1) % tabs.length,
        Home: 0,
        End: tabs.length - 1,
      }[event.key];
      if (destination === undefined) return;
      event.preventDefault();
      select(destination, true);
    });
  });
  bar.hidden = false;

  const shift = document.getElementById('shift-practice');
  const status = document.getElementById('shift-status');
  const times = [...panels[1].querySelectorAll('time')].map(node => ({
    node, label: node.textContent, datetime: node.dateTime,
  }));
  let moved = false;
  let animations = [];
  shift.addEventListener('click', () => {
    moved = !moved;
    animations.forEach(animation => animation.cancel());
    animations = [];
    times.forEach(({node, label, datetime}, index) => {
      node.textContent = moved ? node.dataset.late : label;
      node.dateTime = moved ? node.dataset.clock : datetime;
      if (!reduceMotion.matches) {
        animations.push(node.closest('li').animate([
          { transform: 'translateY(5px)', opacity: .5 },
          { transform: 'translateY(0)', opacity: 1 },
        ], { duration: 250, delay: index * 40, easing: 'ease-out' }));
      }
    });
    shift.textContent = moved ? 'Reset example' : 'Move practice 30 min later';
    status.textContent = moved ? 'Both blocks moved 30 minutes later.' : '';
  });

  const clock = document.getElementById('focus-clock');
  const toggle = document.getElementById('focus-toggle');
  const steps = [...panels[2].querySelectorAll('input[type="checkbox"]')];
  const focusStatus = document.getElementById('focus-status');
  let remaining = 25 * 60 * 1000;
  let deadline = 0;
  let ticker;
  function renderTime() {
    const seconds = Math.ceil(remaining / 1000);
    clock.textContent = Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
  }
  function stop() {
    clearInterval(ticker);
    ticker = undefined;
    toggle.textContent = remaining ? 'Resume timer' : 'Restart timer';
  }
  function tick() {
    remaining = Math.max(0, deadline - performance.now());
    renderTime();
    if (!remaining) stop();
  }
  toggle.addEventListener('click', () => {
    if (ticker !== undefined) {
      tick();
      stop();
      return;
    }
    if (!remaining) remaining = 25 * 60 * 1000;
    deadline = performance.now() + remaining;
    ticker = setInterval(tick, 250);
    toggle.textContent = 'Pause timer';
    tick();
  });
  steps.forEach(step => step.addEventListener('change', () => {
    focusStatus.textContent = steps.filter(item => item.checked).length + ' of ' + steps.length + ' steps complete';
  }));

  const homeLink = document.querySelector('.nav-links a[href="#home"]');
  const faqLink = document.querySelector('.nav-links a[href="#faq"]');
  function updateNav() {
    const onFaq = location.hash === '#faq';
    (onFaq ? homeLink : faqLink).removeAttribute('aria-current');
    (onFaq ? faqLink : homeLink).setAttribute('aria-current', 'location');
  }
  window.addEventListener('hashchange', updateNav);
  updateNav();

  const plani = document.getElementById('plani');
  const shadow = document.querySelector('.plani-shadow');
  let planiMotion;
  let shadowMotion;
  function greet() {
    if (reduceMotion.matches) return;
    planiMotion?.cancel();
    shadowMotion?.cancel();
    planiMotion = plani.animate([
      { transform: 'translateY(0) rotate(0)' },
      { transform: 'translateY(-8px) rotate(-4deg)', offset: .32 },
      { transform: 'translateY(1px) rotate(2deg)', offset: .75 },
      { transform: 'none' },
    ], { duration: 900, easing: 'ease-in-out' });
    shadowMotion = shadow.animate([
      { transform: 'scale(1)', opacity: 1 },
      { transform: 'scale(.88)', opacity: .68, offset: .32 },
      { transform: 'scale(1)', opacity: 1 },
    ], { duration: 900, easing: 'ease-in-out' });
  }

  const hint = document.getElementById('hint-example');
  hint.addEventListener('toggle', () => {
    if (hint.open) greet();
  });

  const finalScore = document.getElementById('final-score');
  const finalScoreValue = document.getElementById('final-score-value');
  const gradeResult = document.getElementById('grade-result');
  document.getElementById('grade-control').hidden = false;
  document.getElementById('grade-result-label').textContent = 'Projected course grade';
  finalScore.addEventListener('input', () => {
    const score = finalScore.valueAsNumber;
    const projectedGrade = (86 * 75 + score * 25) / 100;
    finalScore.setAttribute('aria-valuetext', score + '%');
    finalScoreValue.textContent = score + '%';
    gradeResult.value = projectedGrade.toLocaleString('en-US', { maximumFractionDigits: 2 }) + '%';
  });

  const sections = new IntersectionObserver(entries => {
    entries.forEach(({ target, isIntersecting }) => {
      if (!isIntersecting) return;
      sections.unobserve(target);
      if (reduceMotion.matches) return;
      target.animate([
        { opacity: .65, transform: 'translateY(12px)' },
        { opacity: 1, transform: 'none' },
      ], { duration: 420, easing: 'ease-out' });
      if (target.contains(plani)) greet();
    });
  }, { threshold: .18 });
  document.querySelectorAll('[data-reveal]').forEach(element => sections.observe(element));
})();
