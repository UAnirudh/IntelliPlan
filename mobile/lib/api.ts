import * as SecureStore from "expo-secure-store";
import { API_BASE } from "./config";

const TOKEN_KEY = "ip_bearer";

export async function getToken(): Promise<string | null> {
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export async function setToken(token: string | null): Promise<void> {
  if (token === null) {
    await SecureStore.deleteItemAsync(TOKEN_KEY);
    return;
  }
  await SecureStore.setItemAsync(TOKEN_KEY, token);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const headers = new Headers(init.headers || {});
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.error || body?.message || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return null as T;
  return (await res.json()) as T;
}

export async function login(email: string, password: string): Promise<string> {
  const data = await apiFetch<{ token: string }>("/api/v1/auth/token", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  await setToken(data.token);
  return data.token;
}

/* ── Domain calls ──────────────────────────────────────────────────────
   One place per endpoint, so a screen never builds a URL. The v1 API
   proxies each of these to the same view the website renders, which is
   what makes the phone show data from whichever platforms the student
   actually connected — Canvas, StudentVue, Schoology, Classroom — rather
   than a separate half-implementation that drifts.
   ─────────────────────────────────────────────────────────────────── */

export type Assignment = {
  title: string;
  course?: string;
  due_date?: string;
  points?: number | string;
  priority?: string;
  estimated_time?: number;
  platform?: string;
  dismissed?: boolean;
};

export type Course = { name?: string; id?: string | number; [k: string]: unknown };

export type CourseGrade = {
  course?: string;
  name?: string;
  grade?: string | number;
  percent?: number | string;
  letter?: string;
  [k: string]: unknown;
};

export type ScheduleBlock = {
  title?: string;
  course?: string;
  start?: string;
  end?: string;
  time_slot?: string;
  day?: string;
  date?: string;
  [k: string]: unknown;
};

export async function logout(): Promise<void> {
  await setToken(null);
}

/**
 * Everything due, from every source.
 *
 * /api/v1/tasks rather than /api/v1/assignments: the latter proxies the
 * live platform feed only, so a manual task the student just added — or
 * anything synced from Notion — would be missing from the list they added
 * it in.
 */
export async function getAssignments(): Promise<Assignment[]> {
  const d = await apiFetch<{ tasks?: Assignment[] }>("/api/v1/tasks");
  return d?.tasks || [];
}

export async function getCourses(): Promise<Course[]> {
  const d = await apiFetch<{ courses?: Course[] }>("/api/v1/courses");
  return d?.courses || [];
}

export async function getGrades(): Promise<any> {
  return apiFetch<any>("/api/v1/grades");
}

export async function getSchedule(): Promise<any> {
  return apiFetch<any>("/api/v1/schedule");
}

/**
 * Tick an assignment off.
 *
 * Keyed by title because that is what the web `/dismiss` view takes —
 * assignments arrive from several platforms and have no shared id, so the
 * title is the only key every source agrees on.
 */
export async function dismissAssignment(title: string): Promise<void> {
  await apiFetch("/api/v1/assignments/dismiss", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export async function restoreAssignment(title: string): Promise<void> {
  await apiFetch("/api/v1/assignments/restore", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export async function generateSchedule(
  hoursPerDay: number,
  preferredTime: string,
): Promise<any> {
  return apiFetch<any>("/api/v1/schedule/generate", {
    method: "POST",
    body: JSON.stringify({ hours_per_day: hoursPerDay, preferred_time: preferredTime }),
  });
}
