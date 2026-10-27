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
  const button = nodes.find((node) => node.tagName === 'button');
  assert.ok(answer.hidden);
  assert.equal(button.textContent, 'Show answer');
  button.onclick();
  assert.equal(answer.hidden, false);
  assert.equal(button.textContent, 'Hide answer');
  assert.equal(section.innerHTML, undefined);
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
