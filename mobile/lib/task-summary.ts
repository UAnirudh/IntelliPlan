export type TaskListFilter = "all" | "overdue" | "today" | "week" | "done";

/** Keep the Due screen's header in step with the filters the student sees. */
export function taskListSummary(
  visibleCount: number,
  filter: TaskListFilter,
  courseLabel?: string,
): string {
  const state = filter === "done" ? "completed" : "open";
  const item = visibleCount === 1 ? "item" : "items";
  const course = courseLabel ? ` · ${courseLabel}` : "";
  return `${visibleCount} ${state} ${item}${course}`;
}

/** Keep a selected course filter visible so the student can always clear it. */
export function shouldShowCourseFilter(courseCount: number, selectedCourse: string): boolean {
  return courseCount > 2 || selectedCourse !== "__all";
}
