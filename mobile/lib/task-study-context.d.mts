export type TaskStudyContext = {
  taskTitle?: string;
  course?: string;
  notes?: string;
};

export const MIN_STUDY_MATERIAL_CHARS: number;

export function taskStudyParams(task: {
  title?: unknown;
  course?: unknown;
  notes?: unknown;
} | null | undefined): TaskStudyContext;

export function taskStudyMaterial(context: TaskStudyContext | null | undefined): string;

export function studyGenerationInput(context: TaskStudyContext & {
  text?: string;
  initialMaterial?: string;
}): { content: string; kind: "materials" | "topic" } | null;
