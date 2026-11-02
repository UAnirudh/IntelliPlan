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
