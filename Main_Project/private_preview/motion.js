(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const orbit = document.getElementById('preview-orbit');
  const motion = document.getElementById('motion-toggle');
  const caption = document.getElementById('orbit-copy');
  const planets = Array.from(orbit.querySelectorAll('.planet')).map(button => ({
    button, angle: Number(button.dataset.angle), inner: button.dataset.ring === 'inner',
  }));
  const tilt = -23 * Math.PI / 180;
  let angle = 0;
  let width = 0;
  let height = 0;
  let paused = reduceMotion.matches;
  let frame = 0;
  let last = null;
  let visible = true;
  let drag = null;
  let heldPlanet = false;
  let heldFocus = false;

  function render() {
    for (const {button, angle: start, inner} of planets) {
      const phase = start + angle;
      const depth = Math.sin(phase);
      const x = Math.cos(phase) * width * (inner ? .265 : .38);
      const y = depth * height * (inner ? .165 : .245);
      button.style.setProperty('--orbit-x', (x * Math.cos(tilt) - y * Math.sin(tilt)).toFixed(2) + 'px');
      button.style.setProperty('--orbit-y', (x * Math.sin(tilt) + y * Math.cos(tilt)).toFixed(2) + 'px');
      button.style.setProperty('--depth', (1 + .045 * depth).toFixed(3));
      button.style.zIndex = String(Math.round(11 + depth * 4));
    }
  }

  function tick(now) {
    if (last !== null) angle += Math.min(now - last, 48) * .00032;
    last = now;
    render();
    frame = requestAnimationFrame(tick);
  }

  function reconcile() {
    const running = !paused && visible && !heldPlanet && !heldFocus && !drag && !document.hidden;
    if (running && !frame) frame = requestAnimationFrame(tick);
    if (!running && frame) {
      cancelAnimationFrame(frame);
      frame = 0;
      last = null;
    }
    motion.textContent = paused ? 'Play motion' : 'Pause motion';
  }

  function measure() {
    width = orbit.clientWidth;
    height = orbit.clientHeight;
    render();
  }

  orbit.classList.add('ready');
  measure();
  new ResizeObserver(measure).observe(orbit);
  new IntersectionObserver(entries => {
    visible = entries[0].isIntersecting;
    reconcile();
  }).observe(orbit);
  document.addEventListener('visibilitychange', reconcile);
  reduceMotion.addEventListener('change', () => {
    paused = reduceMotion.matches;
    reconcile();
  });
  motion.hidden = false;
  motion.addEventListener('click', () => {
    paused = !paused;
    reconcile();
  });

  for (const {button} of planets) {
    button.addEventListener('pointerenter', event => {
      if (event.pointerType === 'mouse') {
        heldPlanet = true;
        reconcile();
      }
    });
    button.addEventListener('pointerleave', () => {
      heldPlanet = false;
      reconcile();
    });
    button.addEventListener('focus', () => { heldFocus = true; reconcile(); });
    button.addEventListener('blur', () => { heldFocus = false; reconcile(); });
    button.addEventListener('click', () => {
      for (const item of planets) item.button.setAttribute('aria-pressed', String(item.button === button));
      caption.textContent = button.dataset.copy;
    });
  }

  // Drag empty space to rotate without taking over vertical touch scrolling.
  orbit.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.target.closest('button') || drag) return;
    drag = {id: event.pointerId, start: event.clientX, angle};
    orbit.setPointerCapture(event.pointerId);
    orbit.classList.add('dragging');
    reconcile();
  });
  orbit.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    angle = drag.angle + (event.clientX - drag.start) * .014;
    render();
  });
  orbit.addEventListener('lostpointercapture', () => {
    drag = null;
    orbit.classList.remove('dragging');
    reconcile();
  });
  reconcile();

  const shift = document.getElementById('shift-practice');
  const result = document.getElementById('sample-status');
  const times = Array.from(document.querySelectorAll('.schedule-row time')).map(element => ({
    element, original: element.dateTime,
  }));
  let moved = false;
  let animations = [];
  shift.hidden = false;
  shift.addEventListener('click', () => {
    moved = !moved;
    animations.forEach(animation => animation.cancel());
    animations = [];
    times.forEach(({element, original}, index) => {
      element.textContent = moved ? element.dataset.change : element.dataset.original;
      element.dateTime = moved ? element.dataset.time : original;
      if (reduceMotion.matches) return;
      animations.push(element.closest('li').animate([
        {transform: 'translateX(13px)', opacity: .39},
        {transform: 'translateX(0)', opacity: 1},
      ], {duration: 380, delay: index * 45, easing: 'cubic-bezier(.2,.65,.34,1)', fill: 'backwards'}));
    });
    shift.firstChild.textContent = moved ? 'Reset the sample ' : 'Practice runs late ';
    result.textContent = moved ? 'Both study blocks moved 30 minutes later.' : 'Try moving practice 30 minutes later.';
  });

  const sample = document.getElementById('week');
  const entrance = new IntersectionObserver(entries => {
    if (!entries[0].isIntersecting) return;
    if (!reduceMotion.matches) sample.querySelector('.plan-card').animate([
      { opacity: .3, transform: 'translateY(18px)' },
      { opacity: 1, transform: 'translateY(0)' },
    ], {duration: 600, easing: 'cubic-bezier(.22,.61,.36,1)'});
    entrance.disconnect();
  }, {threshold: .18});
  entrance.observe(sample);
})();
