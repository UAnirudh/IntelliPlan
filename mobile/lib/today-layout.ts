/** Width for the left rail of the Today agenda's time-ordered study blocks. */
export function agendaTimeColumnWidth(viewportWidth: number): number {
  if (viewportWidth < 360) return 58;
  if (viewportWidth < 400) return 64;
  return 72;
}
