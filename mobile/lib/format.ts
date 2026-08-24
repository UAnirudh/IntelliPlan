/** Date and duration helpers shared across screens. */

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function parseDate(raw?: string | null): Date | null {
  if (!raw) return null;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Whole days from today. Negative is in the past. */
export function daysAway(raw?: string | null): number | null {
  const d = parseDate(raw);
  if (!d) return null;
  return Math.round((startOfDay(d) - startOfDay(new Date())) / 86_400_000);
}

/**
 * "Due tomorrow", "3d overdue".
 *
 * Falls back to echoing the raw string: several platforms send prose
 * ("End of week") rather than a timestamp, and showing that verbatim beats
 * showing "Invalid Date".
 */
export function dueLabel(raw?: string | null): string {
  if (!raw) return "No due date";
  const days = daysAway(raw);
  if (days === null) return String(raw);
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  if (days === -1) return "1 day overdue";
  if (days < 0) return `${Math.abs(days)} days overdue`;
  if (days <= 7) return `Due in ${days} days`;
  return `Due ${parseDate(raw)!.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

export function shortDate(raw?: string | null): string {
  const d = parseDate(raw);
  if (!d) return "";
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export function weekdayShort(raw?: string | null): string {
  const d = parseDate(raw);
  if (!d) return "";
  return d.toLocaleDateString(undefined, { weekday: "short" });
}

export function clockTime(raw?: string | null): string {
  const d = parseDate(raw);
  if (!d) return String(raw ?? "");
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** 95 → "1h 35m". */
export function minutes(n?: number | string | null): string {
  const v = typeof n === "string" ? parseFloat(n) : n;
  if (!v || !Number.isFinite(v) || v <= 0) return "";
  const h = Math.floor(v / 60);
  const m = Math.round(v % 60);
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return `${m}m`;
}

/** Percentages arrive as 87, "87", or "87%" depending on the platform. */
export function toPercent(raw: unknown): number | null {
  if (raw === undefined || raw === null || raw === "") return null;
  const n = typeof raw === "number" ? raw : parseFloat(String(raw).replace("%", "").trim());
  return Number.isFinite(n) ? n : null;
}

export function letterFor(p: number | null): string {
  if (p === null) return "—";
  if (p >= 90) return "A";
  if (p >= 80) return "B";
  if (p >= 70) return "C";
  if (p >= 60) return "D";
  return "F";
}

export function titleCase(s?: string | null): string {
  if (!s) return "";
  return s.charAt(0).toUpperCase() + s.slice(1);
}
