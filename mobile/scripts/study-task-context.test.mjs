import test from "node:test";
import assert from "node:assert/strict";
import {
  MIN_STUDY_MATERIAL_CHARS,
  studyGenerationInput,
  taskStudyMaterial,
} from "../lib/task-study-context.mjs";

test("a task title and course become explicit topic-based practice even when labels exceed the text minimum", () => {
  const initialMaterial = taskStudyMaterial({
    taskTitle: "Biology: Cell Structure",
    course: "Biology",
  });
  assert.ok(initialMaterial.length >= MIN_STUDY_MATERIAL_CHARS);

  assert.deepEqual(
    studyGenerationInput({
      text: initialMaterial,
      initialMaterial,
      taskTitle: "Biology: Cell Structure",
      course: "Biology",
    }),
    { content: initialMaterial, kind: "topic" },
  );
});

test("attached notes use the grounded materials path only when there is enough source text", () => {
  const notes = "Mitochondria release energy through cellular respiration. ".repeat(2);
  const initialMaterial = taskStudyMaterial({
    taskTitle: "Cell structure",
    course: "Biology",
    notes,
  });

  assert.deepEqual(
    studyGenerationInput({
      text: initialMaterial,
      initialMaterial,
      taskTitle: "Cell structure",
      course: "Biology",
      notes,
    }),
    { content: initialMaterial, kind: "materials" },
  );
});

test("student-added source material takes precedence over short task context", () => {
  const initialMaterial = taskStudyMaterial({ taskTitle: "Cell structure", course: "Biology" });
  const text = `${initialMaterial}\n\n${"The cell membrane controls what enters and leaves the cell. ".repeat(2)}`;
  assert.deepEqual(
    studyGenerationInput({
      text,
      initialMaterial,
      taskTitle: "Cell structure",
      course: "Biology",
    }),
    { content: text.trim(), kind: "materials" },
  );
});

test("a short student addition stays topic-based instead of being misrepresented as class material", () => {
  const initialMaterial = taskStudyMaterial({ taskTitle: "Cell structure", course: "Biology" });
  const shortAddition = "Review cell membranes";
  const result = studyGenerationInput({
    text: `${initialMaterial}\n\n${shortAddition}`,
    initialMaterial,
    taskTitle: "Cell structure",
    course: "Biology",
  });

  assert.equal(result.kind, "topic");
  assert.ok(result.content.includes(shortAddition));
});

test("short material without task context cannot be sent to generate", () => {
  assert.equal(
    studyGenerationInput({ text: "Too short for practice", initialMaterial: "" }),
    null,
  );
});
