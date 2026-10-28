const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const template = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/tutor.html'), 'utf8');
const source = template.slice(
  template.indexOf('function parseStructuredStudyJson(content) {'),
  template.indexOf('function renderArtifact(segment) {'),
);
const context = vm.createContext({ JSON, String });
vm.runInContext(`${source}\nglobalThis.parse = parseStructuredStudyJson;`, context);

test('summary-only JSON renders as readable text instead of falling back to raw JSON', () => {
  const result = context.parse(JSON.stringify({ title: 'Cell structure', summary: 'The nucleus stores DNA.' }));
  assert.deepEqual(JSON.parse(JSON.stringify(result)), [
    { kind: 'text', text: '### Cell structure\n\nThe nucleus stores DNA.' },
  ]);
});

test('fenced summary-only JSON renders as readable text', () => {
  const result = context.parse('```json\n{"summary":"Mitosis creates two identical cells."}\n```');
  assert.deepEqual(JSON.parse(JSON.stringify(result)), [
    { kind: 'text', text: 'Mitosis creates two identical cells.' },
  ]);
});

test('structured summaries accompany validated flashcards', () => {
  const result = context.parse(JSON.stringify({
    title: 'Biology review',
    summary: 'Focus on the stages of mitosis.',
    cards: [{ question: 'What does mitosis create?', answer: 'Two identical daughter cells.' }],
  }));
  const plain = JSON.parse(JSON.stringify(result));
  assert.equal(plain[0].kind, 'text');
  assert.match(plain[0].text, /Focus on the stages of mitosis/);
  assert.equal(plain[1].kind, 'artifact');
  assert.equal(plain[1].type, 'flashcards');
});

test('unknown structured JSON still uses the safe plain-text fallback', () => {
  assert.equal(context.parse('{"status":"ok"}'), null);
});
