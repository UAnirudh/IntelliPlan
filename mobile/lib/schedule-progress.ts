import type { ScheduleProgress } from "./api";

/** Apply one completion toggle to the latest complete progress snapshot. */
export function toggleScheduleBlockProgress(
  progress: ScheduleProgress,
  blockId: string,
): ScheduleProgress {
  const current = progress[blockId];
  return {
    ...progress,
    [blockId]: { ...(current || {}), done: !current?.done },
  };
}

/**
 * A schedule read may have started before the student changed local progress.
 * Ignore its progress snapshot even when every optimistic save has since
 * completed; the response is still older than the local interaction.
 */
export function canApplyServerProgress(
  requestRevision: number,
  currentRevision: number,
  pendingWrites: number,
): boolean {
  return requestRevision === currentRevision && pendingWrites === 0;
}

/**
 * The progress endpoint replaces the full JSON map. Serialize writes so a
 * slower earlier request cannot finish after a newer tap and overwrite it.
 */
export function createProgressSaveQueue(
  save: (progress: ScheduleProgress) => Promise<void>,
): (progress: ScheduleProgress) => Promise<void> {
  let pending: Promise<void> = Promise.resolve();
  return (progress) => {
    const next = pending.catch(() => undefined).then(() => save(progress));
    pending = next;
    return next;
  };
}
