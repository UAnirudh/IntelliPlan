const { test } = require('node:test');
const assert = require('node:assert/strict');

function makeNode(tag) {
  return {
    tagName: tag,
    children: [],
    attributes: {},
    style: {},
    hidden: false,
    textContent: '',
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    setAttribute(key, value) { this.attributes[key] = String(value); },
    addEventListener(type, handler) { this[`on${type}`] = handler; },
  };
}

global.document = { createElement: makeNode, createElementNS: (_ns, tag) => makeNode(tag) };
global.window = {};
require('../../static/js/tutor-artifacts.js');

function all(root) {
  return [root, ...(root.children || []).flatMap(all)];
}

test('flashcard artifacts reveal validated text without inserting generated HTML', () => {
  const hostile = '<img src=x onerror="steal()">';
  const section = window.IPTutorArtifacts.create({
    type: 'flashcards', title: 'Biology cards',
    content: JSON.stringify({ cards: [{ question: hostile, answer: 'Mitochondria [N1]' }] }),
  });
  const nodes = all(section);
  assert.ok(nodes.some((node) => node.textContent === hostile));
  const answer = nodes.find((node) => node.tagName === 'p' && node.textContent === 'Mitochondria [N1]');
  const button = nodes.find((node) => node.tagName === 'button' && node.textContent === 'Show answer');
  assert.ok(answer.hidden);
  assert.equal(button.textContent, 'Show answer');
  button.onclick();
  assert.equal(answer.hidden, false);
  assert.equal(button.textContent, 'Hide answer');
  assert.equal(section.innerHTML, undefined);
});

test('data plot keeps signed values accessible and rejects unordered inputs', () => {
  const config = {xLabel: 'Time', yLabel: 'Position', points: [[0, -2], [1, 3], [2, 8]]};
  const render = points => window.IPTutorArtifacts.create({
    type: 'data-plot', title: 'Motion', content: JSON.stringify({...config, points}),
  });
  assert.deepEqual(all(render(config.points)).filter(node => node.tagName === 'td')
    .map(node => node.textContent), ['0', '-2', '1', '3', '2', '8']);
  assert.ok(all(render([[2, 3], [1, 4]])).every(node => node.tagName !== 'svg'));
});

test('roadmaps keep dates consecutive and link only reviewed sources', () => {
  global.document.getElementById = () => ({textContent: JSON.stringify({
    'python-start': {title: 'Python tutorial', url: 'https://docs.python.org/3/tutorial/'},
  })});
  const stage = {title: 'Python', days: 2, lessons: ['Read the examples'],
    practice: 'Solve two problems', project: 'Build a calculator',
    exit: 'Test two unseen inputs', review: 'Repeat one problem in two days'};
  const config = {start: '2026-12-30', stages: [
    {...stage, resource: 'python-start'},
    {...stage, title: 'Algebra', resource: 'https://unknown.example'},
  ]};
  const section = window.IPTutorArtifacts.create({
    type: 'study-roadmap', title: 'Learning plan', content: JSON.stringify(config),
  });
  const links = all(section).filter(node => node.tagName === 'a');
  assert.deepEqual(links.map(node => node.href), ['https://docs.python.org/3/tutorial/']);
  assert.match(window.IPTutorArtifacts.roadmapText(config), /Jan 1, 2027/);
  assert.match(window.IPTutorArtifacts.roadmapText(config), /Test two unseen inputs/);
  assert.equal(window.IPTutorArtifacts.roadmapText({...config, start: '2027-02-29'}), null);
});

test('flashcard deck moves one card at a time and resets answer state', () => {
  const section = window.IPTutorArtifacts.create({
    type: 'flashcards', title: 'Biology cards',
    content: JSON.stringify({ cards: [
      { question: 'What is ATP?', answer: 'The cell\'s main energy currency.' },
      { question: 'Where is DNA stored?', answer: 'Mostly in the nucleus.' },
    ] }),
  });
  const nodes = all(section);
  const buttons = nodes.filter((node) => node.tagName === 'button');
  const previous = buttons.find((node) => node.attributes['aria-label'] === 'Previous flashcard');
  const next = buttons.find((node) => node.attributes['aria-label'] === 'Next flashcard');
  const reveal = buttons.find((node) => node.textContent === 'Show answer');
  const question = nodes.find((node) => node.tagName === 'h3');
  const answer = nodes.find((node) => node.tagName === 'p' && node.textContent === "The cell's main energy currency.");
  const count = nodes.find((node) => node.tagName === 'small' && node.className === 'tutor-flashcard-count');
  const progress = nodes.find((node) => node.className === 'tutor-flashcard-progress');

  assert.equal(previous.disabled, true);
  assert.equal(next.disabled, false);
  assert.equal(count.textContent, 'Card 1 of 2');
  assert.equal(progress.attributes['aria-valuenow'], '1');
  assert.equal(question.textContent, 'What is ATP?');
  assert.equal(answer.hidden, true);
  reveal.onclick();
  assert.equal(answer.hidden, false);
  next.onclick();
  assert.equal(question.textContent, 'Where is DNA stored?');
  assert.equal(count.textContent, 'Card 2 of 2');
  assert.equal(progress.attributes['aria-valuenow'], '2');
  assert.equal(answer.hidden, true);
  assert.equal(previous.disabled, false);
  assert.equal(next.disabled, true);
  previous.onclick();
  assert.equal(question.textContent, 'What is ATP?');
  assert.equal(answer.hidden, true);
});

test('invalid flashcard JSON stays in a text-only fallback', () => {
  const raw = '<script>doNotRun()</script>';
  const section = window.IPTutorArtifacts.create({
    type: 'flashcards', title: 'Bad cards', content: JSON.stringify({ cards: [{ question: raw }] }),
  });
  const nodes = all(section);
  assert.ok(nodes.some((node) => node.textContent.includes('could not be opened')));
  assert.ok(nodes.some((node) => node.tagName === 'pre' && node.textContent.includes(raw)));
  assert.equal(section.innerHTML, undefined);
});
