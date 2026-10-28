function clean(value) {
  return typeof value === "string" ? value.trim() : "";
}

export const MIN_STUDY_MATERIAL_CHARS = 40;

/** Build the only task fields that may cross into Study Tools. */
export function taskStudyParams(task) {
  const params = {};
  const title = clean(task?.title);
  const course = clean(task?.course);
  const notes = clean(task?.notes);
  if (title) params.taskTitle = title;
  if (course) params.course = course;
  if (notes) params.notes = notes;
  return params;
}

/** Present task context as editable study material without implying retrieval. */
export function taskStudyMaterial(context) {
  const title = clean(context?.taskTitle);
  const course = clean(context?.course);
  const notes = clean(context?.notes);
  const sections = [];
  if (title) sections.push(`Task: ${title}`);
  if (course) sections.push(`Course: ${course}`);
  if (notes) sections.push(`Notes:\n${notes}`);
  return sections.join("\n\n");
}

/**
 * Choose between grounded material and a clearly labeled topic starting point.
 * A task's generated title/course handoff is not source material, even when
 * those labels happen to exceed the study tool's minimum character count.
 */
export function studyGenerationInput({
  text,
  initialMaterial,
  taskTitle,
  course,
  notes,
}) {
  const material = clean(text);
  const attachedNotes = clean(notes);
  const title = clean(taskTitle);
  const taskContext = !!(title || clean(course) || attachedNotes);
  const initial = clean(initialMaterial);
  const isUneditedTaskHandoff = taskContext && material === initial;
  const addedMaterial = initial && material.startsWith(initial)
    ? material.slice(initial.length).trim()
    : material;
  const groundedMaterialLength = isUneditedTaskHandoff ? attachedNotes.length : addedMaterial.length;

  if (material.length >= MIN_STUDY_MATERIAL_CHARS && groundedMaterialLength >= MIN_STUDY_MATERIAL_CHARS) {
    return { content: material, kind: "materials" };
  }

  // Preserve the task's short notes as clues, but don't label the result as
  // grounded practice unless the student supplied enough actual material.
  if (taskContext && (title || clean(course))) {
    const topicNotes = [attachedNotes, addedMaterial].filter(Boolean).join("\n");
    const topic = taskStudyMaterial({ taskTitle: title, course, notes: topicNotes });
    return { content: topic, kind: "topic" };
  }

  return null;
}
