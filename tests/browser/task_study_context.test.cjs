const { test } = require('node:test');
const assert = require('node:assert/strict');

let context;
test('task study handoff includes only the selected title, course, and available notes', async () => {
  context ||= await import('../../mobile/lib/task-study-context.mjs');
  const params = context.taskStudyParams({
    title: '  Cell structure review  ',
    course: '  Biology  ',
    notes: '  Read chapter 7 and label the organelles.  ',
    due_date: '2026-11-04',
    id: 'school-assignment-123',
  });

  assert.deepEqual(params, {
    taskTitle: 'Cell structure review',
    course: 'Biology',
    notes: 'Read chapter 7 and label the organelles.',
  });
});

test('task study material is clearly labeled and preserves available notes', async () => {
  context ||= await import('../../mobile/lib/task-study-context.mjs');
  assert.equal(
    context.taskStudyMaterial({
      taskTitle: 'Cell structure review',
      course: 'Biology',
      notes: 'Read chapter 7.',
    }),
    'Task: Cell structure review\n\nCourse: Biology\n\nNotes:\nRead chapter 7.',
  );
});

test('task study works without notes and omits blank or unrelated values', async () => {
  context ||= await import('../../mobile/lib/task-study-context.mjs');
  const params = context.taskStudyParams({
    title: '  Review for quiz  ',
    course: '',
    notes: null,
    due_date: '2026-11-04',
  });

  assert.deepEqual(params, { taskTitle: 'Review for quiz' });
  assert.equal(context.taskStudyMaterial(params), 'Task: Review for quiz');
});

test('plain Study Tools still starts without prefilled material', async () => {
  context ||= await import('../../mobile/lib/task-study-context.mjs');
  assert.deepEqual(context.taskStudyParams(undefined), {});
  assert.equal(context.taskStudyMaterial(undefined), '');
});
