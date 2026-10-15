/* Declarative exercises: model output supplies data, never executable code. */
(function () {
  'use strict';
  var SVG = 'http://www.w3.org/2000/svg';
  function element(tag, text, parent) {
    var node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (parent) parent.appendChild(node);
    return node;
  }
  function shape(tag, attrs, parent) {
    var node = document.createElementNS(SVG, tag);
    Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    parent.appendChild(node);
    return node;
  }
  function number(value, fallback, min, max) {
    return typeof value === 'number' && Number.isFinite(value)
      ? Math.max(min, Math.min(max, value)) : fallback;
  }
  function label(value, fallback) {
    return typeof value === 'string' && value.trim() ? value.slice(0, 500) : fallback;
  }
  function visual(config, wrap) {
    var fraction = config.kind === 'fraction';
    if (!fraction && config.kind !== 'linear') throw new Error('Unknown visual');
    element('p', label(config.question, fraction
      ? 'Predict how the shaded share changes when you change the denominator.'
      : 'Predict how the line changes when you change its slope.'), wrap);
    var svg = shape('svg', { viewBox: '0 0 400 240', role: 'img',
      'aria-label': fraction ? 'Fraction model' : 'Interactive line graph' }, wrap);
    svg.style.width = '100%'; svg.style.display = 'block';
    shape('title', {}, svg).textContent = fraction ? 'Shaded fraction' : 'Line: y equals m times x plus b';
    shape('desc', {}, svg).textContent = 'Use the labeled controls below. The text description updates with the diagram.';
    var group = shape('g', {}, svg);
    var controls = element('div', undefined, wrap);
    var output = element('p', undefined, wrap);
    output.setAttribute('aria-live', 'polite');
    function slider(name, initial, min, max, step) {
      var holder = element('label', name + ' ', controls);
      holder.style.display = 'flex'; holder.style.gap = '8px'; holder.style.alignItems = 'center';
      var input = element('input', undefined, holder);
      input.type = 'range'; input.min = min; input.max = max; input.step = step;
      input.value = initial; input.setAttribute('aria-label', name);
      var reading = element('output', input.value, holder);
      input.addEventListener('input', function () { reading.textContent = input.value; draw(); });
      return input;
    }
    var first = slider(fraction ? 'Numerator' : 'Slope', number(config[fraction ? 'numerator' : 'slope'], 1,
      fraction ? 0 : -5, fraction ? 12 : 5), fraction ? 0 : -5, fraction ? 12 : 5, fraction ? 1 : 0.5);
    var second = slider(fraction ? 'Denominator' : 'Intercept', number(config[fraction ? 'denominator' : 'intercept'],
      fraction ? 4 : 0, fraction ? 1 : -5, fraction ? 12 : 5), fraction ? 1 : -5, fraction ? 12 : 5, fraction ? 1 : 0.5);
    function draw() {
      group.replaceChildren();
      var a = Number(first.value), b = Number(second.value);
      if (fraction) {
        a = Math.round(a); b = Math.round(b);
        // Allow improper fractions by drawing as many unit rows as needed.
        var rows = Math.max(1, Math.ceil(a / b));
        var height = Math.min(48, 190 / rows);
        for (var row = 0; row < rows; row++) {
          for (var col = 0; col < b; col++) {
            shape('rect', { x: 20 + col * 360 / b, y: 15 + row * height,
              width: 360 / b, height: height - 3, fill: row * b + col < a ? '#729ce0' : '#25221e',
              stroke: '#b5b0a8', 'stroke-width': 1 }, group);
          }
        }
        output.textContent = a + ' of ' + b + ' equal parts per whole are shaded: ' + a + '/' + b + '.';
      } else {
        shape('path', { d: 'M20 120H380 M200 12V228', stroke: '#b5b0a8', fill: 'none' }, group);
        [['x', 380, 138], ['y', 208, 16], ['0', 208, 138], ['10', 360, 112], ['6', 208, 28]].forEach(function (tick) {
          shape('text', { x: tick[1], y: tick[2], fill: 'currentColor', 'font-size': 12 }, group).textContent = tick[0];
        });
        // Clip mathematically to the visible coordinate square, rather than
        // confusing a steep line with a clamped diagonal.
        var x1 = -10, x2 = 10;
        if (a !== 0) {
          var edges = [(-6 - b) / a, (6 - b) / a].sort(function (x, y) { return x - y; });
          x1 = Math.max(x1, edges[0]); x2 = Math.min(x2, edges[1]);
        }
        if (x1 <= x2) shape('line', { x1: 200 + x1 * 18, y1: 120 - (a * x1 + b) * 18,
          x2: 200 + x2 * 18, y2: 120 - (a * x2 + b) * 18,
          stroke: '#729ce0', 'stroke-width': 3 }, group);
        output.textContent = 'y = ' + a + 'x + (' + b + '). At x = 0, y = ' + b +
          '. Each step right changes y by ' + a + '.';
      }
    }
    draw();
    element('p', 'Try a change, then tell Plani what you noticed.', wrap);
  }
  function quiz(config, wrap) {
    if (!Array.isArray(config.options) || config.options.length < 2 || config.options.length > 6 ||
        config.options.some(function (value) { return typeof value !== 'string'; }) ||
        !Number.isInteger(config.correct) || config.correct < 0 || config.correct >= config.options.length) {
      throw new Error('Invalid quiz');
    }
    element('p', label(config.question, 'Choose an option and explain your thinking.'), wrap);
    var feedback = element('p', 'Choose an option to check your reasoning.', wrap);
    feedback.setAttribute('role', 'status');
    config.options.forEach(function (option, index) {
      var button = element('button', option.slice(0, 300), wrap);
      button.type = 'button'; button.className = 'quiz-option';
      button.addEventListener('click', function () {
        feedback.textContent = index === config.correct
          ? 'That matches this AI-generated check. Tell Plani why you chose it.'
          : 'Try again. What might rule out this option?';
      });
    });
    element('small', 'AI-generated practice; this result is not a scored mastery check.', wrap);
  }
  function create(segment) {
    var wrap = element('section'); wrap.className = 'tutor-artifact';
    var head = element('div', label(segment.title, 'Explore'), wrap);
    head.className = 'tutor-artifact-head';
    var body = element('div', undefined, wrap);
    body.style.padding = '16px';
    try {
      if (segment.content.length > 20000) throw new Error('Too large');
      var config = JSON.parse(segment.content);
      if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('Invalid data');
      if (segment.type === 'svg-exercise') visual(config, body);
      else if (segment.type === 'quiz') quiz(config, body);
      else throw new Error('Unsupported exercise');
    } catch (error) {
      // Old HTML/script artifacts and invalid generated data remain inspectable
      // as text. Never execute generated markup or JavaScript in the page.
      element('p', 'This exercise could not be opened. Ask Plani to try another visual or guide you in chat.', body);
      var details = element('details', undefined, body);
      element('summary', 'View exercise text', details);
      var source = element('pre', String(segment.content).slice(0, 20000), details);
      source.style.whiteSpace = 'pre-wrap'; source.style.overflowWrap = 'anywhere';
    }
    return wrap;
  }
  window.IPTutorArtifacts = { create: create };
}());
