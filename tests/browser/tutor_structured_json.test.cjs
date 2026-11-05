const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const template = fs.readFileSync(path.join(__dirname, '../../Main_Project/templates/tutor.html'), 'utf8');
const source = template.slice(
  template.indexOf('const ARTIFACT_RE = '),
  template.indexOf('async function sendTutorMessage() {'),
);
const context = vm.createContext({ JSON, String, window: { IPRichText: null } });
vm.runInContext(`${source}\nglobalThis.parse = parseStructuredStudyJson; globalThis.readable = readableTutorResponseText;`, context);

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

test('study JSON surrounded by provider prose keeps prose and renders artifacts', () => {
  const result = context.parse(`Here are the cards you requested.\n${JSON.stringify({
    title: 'Cell structure',
    summary: 'The nucleus stores DNA.',
    cards: [{ question: 'What does the nucleus store?', answer: 'DNA.' }],
  })}\nAsk me if you want a harder set.`);
  const plain = JSON.parse(JSON.stringify(result));
  assert.equal(plain[0].text, 'Here are the cards you requested.');
  assert.match(plain[1].text, /The nucleus stores DNA/);
  assert.equal(plain[2].kind, 'artifact');
  assert.equal(plain[2].type, 'flashcards');
  assert.equal(plain[3].text, 'Ask me if you want a harder set.');
});

test('fenced study JSON between prose retains both surrounding sentences', () => {
  const result = context.parse('Start here.\n```json\n{"summary":"Mitosis creates two cells.","cards":[{"question":"What does it create?","answer":"Two cells."}]}\n```\nKeep practicing.');
  const plain = JSON.parse(JSON.stringify(result));
  assert.equal(plain[0].text, 'Start here.');
  assert.equal(plain[1].text, 'Mitosis creates two cells.');
  assert.equal(plain[2].type, 'flashcards');
  assert.equal(plain[3].text, 'Keep practicing.');
});

test('balanced extraction respects braces and escaped quotes inside JSON strings', () => {
  const result = context.parse(`Cards: ${JSON.stringify({ cards: [
    { question: 'What does "{cell}" mean?', answer: 'A cell.' },
  ] })}`);
  const plain = JSON.parse(JSON.stringify(result));
  assert.equal(plain[0].text, 'Cards:');
  assert.equal(plain[1].type, 'flashcards');
  assert.match(plain[1].content, /cell/);
});

test('unrecognized JSON after prose is not mistaken for a study artifact', () => {
  assert.equal(context.parse('Result: {"status":"ok"}'), null);
});

test('unknown structured JSON still uses the safe plain-text fallback', () => {
  assert.equal(context.parse('{"status":"ok"}'), null);
});

for (const [label, envelope] of [
  ['plain', JSON.stringify({
    title: 'Cell structure', summary: 'Review energy production.',
    cards: [{ question: 'What makes ATP?', answer: 'The mitochondrion.' }],
  })],
  ['fenced', `\`\`\`json\n${JSON.stringify({
    title: 'Cell structure', summary: 'Review energy production.',
    cards: [{ question: 'What makes ATP?', answer: 'The mitochondrion.' }],
  })}\n\`\`\``],
]) {
  test(`${label} structured JSON becomes readable speech and copy text`, () => {
    const readable = context.readable(envelope);
    assert.match(readable, /Review energy production/);
    assert.match(readable, /Card 1\. What makes ATP\? Answer: The mitochondrion\./);
    assert.doesNotMatch(readable, /[{}"]|"cards"|"question"|"answer"/);
  });
}

test('automatic speech, Read aloud, and Copy reply share readable artifact text', () => {
  assert.match(template, /speakReply\(readableTutorResponseText\(reply\)\)/);
  assert.match(template, /bubble\._speechSource = readableTutorResponseText\(text\)/);
  assert.match(template, /speakReply\(readableTutorResponseText\(text\)\)/);
  assert.match(template, /const source = readableTutorResponseText\(text\);[\s\S]*?navigator\.clipboard\.writeText/);
});
