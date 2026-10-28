export type TaskStudyContext = {
  taskTitle?: string;
  course?: string;
  notes?: string;
};

export function taskStudyParams(task: {
  title?: unknown;
  course?: unknown;
  notes?: unknown;
} | null | undefined): TaskStudyContext;

export function taskStudyMaterial(context: TaskStudyContext | null | undefined): string;
