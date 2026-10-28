function clean(value) {
  return typeof value === "string" ? value.trim() : "";
}

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
