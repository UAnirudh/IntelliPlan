import { Platform } from "react-native";
import * as SecureStore from "expo-secure-store";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { API_BASE } from "./config";

const TOKEN_KEY = "ip_bearer";

/**
 * The bearer lives in the keychain on a phone, which is the whole point —
 * it is a long-lived, full-scope credential and AsyncStorage is plain
 * text on disk.
 *
 * SecureStore has no web implementation and throws there, which would take
 * `expo start --web` down on the first call. The web build therefore falls
 * back to AsyncStorage (localStorage underneath). That is a weaker store,
 * and it is only ever reached in the browser, where the platform gives us
 * nothing better anyway.
 */
const secureAvailable = Platform.OS !== "web";

export async function getToken(): Promise<string | null> {
  if (!secureAvailable) return AsyncStorage.getItem(TOKEN_KEY);
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export async function setToken(token: string | null): Promise<void> {
  if (token === null) {
    if (!secureAvailable) await AsyncStorage.removeItem(TOKEN_KEY);
    else await SecureStore.deleteItemAsync(TOKEN_KEY);
    return;
  }
  if (!secureAvailable) await AsyncStorage.setItem(TOKEN_KEY, token);
  else await SecureStore.setItemAsync(TOKEN_KEY, token);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public requestId?: string,
  ) {
    super(message);
  }
  /** The endpoint exists but this deployment has the feature switched off. */
  get isMissing() {
    return this.status === 404;
  }
  get isAuth() {
    return this.status === 401 || this.status === 403;
  }
}

/**
 * Fires when a request comes back 401. The auth provider subscribes so a
 * token that expired mid-session bounces the student to the sign-in screen
 * once, instead of every screen independently rendering "unauthorized".
 */
type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();
export function onUnauthorized(fn: Listener): () => void {
  unauthorizedListeners.add(fn);
  return () => unauthorizedListeners.delete(fn);
}

/** Requests hang forever on a captive-portal wifi otherwise. */
const TIMEOUT_MS = 25_000;

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const headers = new Headers(init.headers || {});
  headers.set("Accept", "application/json");
  // FormData sets its own Content-Type, including the multipart boundary.
  // Setting one here — even an empty one — replaces that boundary and the
  // server sees a body it cannot parse, which surfaces as "no file".
  const isForm = typeof FormData !== "undefined" && init.body instanceof FormData;
  if (init.body && !isForm && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers, signal: controller.signal });
  } catch (e: any) {
    if (e?.name === "AbortError") {
      throw new ApiError(0, "The server took too long to answer. Check your connection.");
    }
    throw new ApiError(0, "Can't reach IntelliPlan. Check your connection.");
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    let detail = res.statusText || `Request failed (${res.status})`;
    try {
      const body = await res.json();
      detail = body?.message || body?.error || detail;
    } catch {
      /* HTML error page, or an empty body — the status text is all we have */
    }
    if (res.status === 401) unauthorizedListeners.forEach((fn) => fn());
    throw new ApiError(res.status, detail, res.headers.get("X-Request-Id") || undefined);
  }

  if (res.status === 204) return null as T;
  const text = await res.text();
  if (!text) return null as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(res.status, "The server sent something that wasn't JSON.");
  }
}

/**
 * The same request, with a file.
 *
 * A thin wrapper so call sites read the same as the JSON ones; `apiFetch`
 * recognises FormData and leaves its Content-Type — and so its multipart
 * boundary — alone.
 */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  return apiFetch<T>(path, { method: "POST", body: form as unknown as BodyInit });
}

/* ── Auth ─────────────────────────────────────────────────────────── */

export type Me = { id: number; email: string; name?: string | null };

export async function login(email: string, password: string): Promise<Me> {
  const data = await apiFetch<{ token: string; user: Me }>("/api/v1/auth/token", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  await setToken(data.token);
  return data.user;
}

/**
 * Create an account.
 *
 * /api/auth/register rather than anything under /api/v1: the v1 surface is
 * for callers who already have a credential, and has no way to mint the
 * first one. This is the same endpoint the browser extension signs up
 * through, so an account made here is the same account on the website.
 */
export async function register(email: string, password: string, name?: string): Promise<Me> {
  const data = await apiFetch<{ token: string; user: Me }>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, name }),
  });
  await setToken(data.token);
  return data.user;
}

export async function getMe(): Promise<Me> {
  return apiFetch<Me>("/api/v1/me");
}

export async function logout(): Promise<void> {
  await setToken(null);
}

export async function deleteAccount(): Promise<void> {
  await apiFetch("/account/delete", { method: "POST", body: JSON.stringify({}) });
  await setToken(null);
}

/* ── Today / Command Center ───────────────────────────────────────── */

export type PriorityTier = "critical" | "high" | "medium" | "low" | string;

export type PlannedTask = {
  /** The plan's own stable id — a hash of source and row, not a row id.
   *  Editing a manual task needs `source_ref`, which is the row id. */
  id: string;
  /** The id in the system the task came from. For `source: "manual"` this
   *  is the ManualTask row id the update and delete endpoints take. */
  source_ref?: string;
  title: string;
  course?: string | null;
  due_date?: string | null;
  est_minutes?: number | null;
  status?: string;
  kind?: string;
  source?: string;
  priority?: { score: number; tier: PriorityTier; rationale?: { key: string; weight: number; reason: string }[] };
  why_now?: string;
};

export type ForecastDay = {
  date: string;
  committed_min: number;
  prework_min: number;
  available_min: number;
  stress: number;
};

export type TodayPayload = {
  generated_at?: string;
  student?: { name?: string; grade_level?: string; greeting?: string };
  briefing?: { headline?: string; body?: string; tone?: string; generated_by?: string; cached?: boolean };
  plan?: PlannedTask[];
  forecast?: { days?: ForecastDay[]; heaviest_date?: string | null; summary?: string };
  health?: {
    score: number;
    delta_vs_yesterday: number;
    tier: string;
    components?: { key: string; value: number; impact: number; reason: string }[];
    summary?: string;
  };
};

export async function getToday(): Promise<TodayPayload> {
  return apiFetch<TodayPayload>("/api/today");
}

export async function refreshToday(): Promise<TodayPayload> {
  return apiFetch<TodayPayload>("/api/today/refresh", { method: "POST", body: JSON.stringify({}) });
}

/* ── Tasks & assignments ──────────────────────────────────────────── */

export type Task = {
  id?: string | number;
  title: string;
  course?: string;
  due_date?: string;
  points?: number | string;
  priority?: string;
  estimated_time?: number;
  platform?: string;
  source?: string;
  notes?: string;
  dismissed?: boolean;
  /** Which column of the web dashboard this came from. */
  bucket?: "overdue" | "today" | "upcoming";
};

/** Everything due, including manual and Notion-synced tasks. */
export async function getTasks(): Promise<Task[]> {
  const d = await apiFetch<{ tasks?: Task[] }>("/api/v1/tasks");
  return d?.tasks || [];
}

/** Only what the connected school platforms report. */
export async function getAssignments(): Promise<Task[]> {
  const d = await apiFetch<{ assignments?: Task[] }>("/api/v1/assignments");
  return d?.assignments || [];
}

export type NewTask = {
  title: string;
  course?: string;
  due_date?: string;
  priority?: string;
  estimated_time?: number;
  notes?: string;
};

export async function createTask(task: NewTask): Promise<unknown> {
  return apiFetch("/api/v1/tasks", { method: "POST", body: JSON.stringify(task) });
}

/**
 * Tick an assignment off.
 *
 * Keyed by title because that is what the web `/dismiss` view takes —
 * assignments arrive from several platforms and share no id, so the title
 * is the only key every source agrees on.
 */
export async function dismissTask(title: string): Promise<void> {
  await apiFetch("/api/v1/assignments/dismiss", { method: "POST", body: JSON.stringify({ title }) });
}

export async function restoreTask(title: string): Promise<void> {
  await apiFetch("/api/v1/assignments/restore", { method: "POST", body: JSON.stringify({ title }) });
}

export type Course = { name?: string; id?: string | number; [k: string]: unknown };

export async function getCourses(): Promise<Course[]> {
  const d = await apiFetch<{ courses?: Course[] }>("/api/v1/courses");
  return d?.courses || [];
}

/* ── Tests ────────────────────────────────────────────────────────── */

export async function getTests(): Promise<Task[]> {
  const d = await apiFetch<{ tests?: Task[] }>("/api/v1/tests");
  return d?.tests || [];
}

export async function markTest(title: string): Promise<void> {
  await apiFetch("/api/v1/tests", { method: "POST", body: JSON.stringify({ title }) });
}

export async function unmarkTest(title: string): Promise<void> {
  await apiFetch("/api/v1/tests", { method: "DELETE", body: JSON.stringify({ title }) });
}

/* ── Grades ───────────────────────────────────────────────────────── */

export type GradeRow = {
  course?: string;
  class_name?: string;
  name?: string;
  percentage?: number | string;
  percent?: number | string;
  grade?: string | number;
  letter?: string;
  teacher?: string;
  period?: string;
  source?: string;
  [k: string]: unknown;
};

export async function getGrades(): Promise<GradeRow[]> {
  const d = await apiFetch<{ grades?: GradeRow[] }>("/api/v1/grades");
  return d?.grades || [];
}

export type Prediction = {
  course: string;
  current_percent: number | null;
  predicted_percent: number;
  confidence: number;
  trend: "improving" | "steady" | "slipping" | string;
  narrative: string;
  sample_size: number;
};

export async function getPredictions(): Promise<{ predictions: Prediction[]; message?: string }> {
  const d = await apiFetch<{ predictions?: Prediction[]; message?: string }>("/api/grade-predictions", {
    method: "POST",
    body: JSON.stringify({}),
  });
  return { predictions: d?.predictions || [], message: d?.message };
}

/* ── Schedule ─────────────────────────────────────────────────────── */

export type Block = {
  /** The generator writes `assignment`; older saved plans wrote `title`. */
  assignment?: string;
  title?: string;
  course?: string;
  time_slot?: string;
  start?: string;
  end?: string;
  /** Minutes. `duration_minutes` is what the generator emits. */
  duration_minutes?: number | string;
  duration?: number | string;
  kind?: string;
  /** Stable per-block key the progress map is written against. */
  block_id?: string;
  task_id?: string | number;
  is_break?: boolean;
  /** Set when the block could not be placed in the stated free hours. */
  unplaced?: boolean;
  [k: string]: unknown;
};

export type ScheduleDay = { day?: string; date?: string; blocks?: Block[] };

/** What a block is called, whichever field the plan was written with. */
export function blockTitle(b: Block): string {
  return String(b.assignment || b.title || b.course || "Study block");
}

/** How long a block runs, in minutes, or 0 when it does not say. */
export function blockMinutes(b: Block): number {
  const raw = b.duration_minutes ?? b.duration;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/** Per-block state the student ticked off, keyed by `block_id`. */
export type ScheduleProgress = Record<string, { done?: boolean; checked?: number[] }>;

export type SavedSchedule = {
  /**
   * Both schedule endpoints answer `{status, data: {schedule: [...]}}`, so
   * the days are a level deeper than the top-level `schedule`/`days` keys
   * older callers looked for. Reading only those found nothing and rendered
   * "no plan saved" over a plan that existed.
   */
  status?: string;
  data?: { schedule?: ScheduleDay[]; [k: string]: unknown };
  progress?: ScheduleProgress;
  schedule?: ScheduleDay[] | Record<string, Block[]>;
  days?: ScheduleDay[];
  name?: string;
  created_at?: string;
  [k: string]: unknown;
};

export async function getSchedule(): Promise<SavedSchedule> {
  return apiFetch<SavedSchedule>("/api/v1/schedule");
}

export async function generateSchedule(
  hoursPerDay: number,
  preferredTime: string,
): Promise<SavedSchedule> {
  const d = await apiFetch<SavedSchedule>("/api/v1/schedule/generate", {
    method: "POST",
    body: JSON.stringify({ hours_per_day: hoursPerDay, preferred_time: preferredTime }),
  });
  // "No assignments to schedule." and "The AI returned an invalid schedule."
  // both come back as HTTP 200 with status: "error". Left unchecked the app
  // reports a successful rebuild and then shows an empty plan.
  if (d?.status === "error") {
    throw new ApiError(400, String((d as any).message || "Couldn't build a plan just now."));
  }
  return d;
}

/**
 * Record which blocks are done.
 *
 * The whole map is sent, not a delta: the endpoint replaces
 * `progress_json` wholesale, so sending one block would erase the rest.
 */
export async function saveScheduleProgress(progress: ScheduleProgress): Promise<void> {
  await apiFetch("/schedule/progress", {
    method: "POST",
    body: JSON.stringify({ progress }),
  });
}

export type ScheduleChange = {
  kind?: string;
  title?: string;
  from?: string;
  to?: string;
  detail?: string;
  [k: string]: unknown;
};

export type Recovery = {
  status?: string;
  changed?: boolean;
  message?: string;
  data?: { schedule?: ScheduleDay[]; [k: string]: unknown };
  missed_minutes?: number;
  completed_minutes?: number;
  missed?: { title?: string; day?: string; minutes?: number }[];
  changes?: ScheduleChange[];
  overloaded?: boolean;
};

/**
 * Re-solve the plan from where the student actually is.
 *
 * The server needs the current assignments passed in — it will not re-fetch
 * them — because a recovery has to be solved against the same work the
 * student is looking at, not whatever a later sync happens to return.
 *
 * Answers `changed: false` when nothing slipped, which is a real answer and
 * not a failure: re-solving an on-track plan just moves blocks the student
 * has already made peace with.
 */
export async function recoverSchedule(args: {
  assignments: Task[];
  hoursPerDay?: number;
  preferredTime?: string;
}): Promise<Recovery> {
  const d = await apiFetch<Recovery>("/schedule/recover", {
    method: "POST",
    body: JSON.stringify({
      assignments: args.assignments,
      hours_per_day: args.hoursPerDay ?? 2,
      preferred_time: args.preferredTime || "evening",
    }),
  });
  if (d?.status === "error") {
    throw new ApiError(400, String(d.message || "Couldn't catch the plan up."));
  }
  return d;
}

/* ── Streak / sparks ──────────────────────────────────────────────── */

export type Streak = {
  status?: string;
  total_points?: number;
  spark_balance?: number;
  sparks_earned_total?: number;
  level?: number;
  level_title?: string;
  next_level?: { level?: number; at?: number; sparks?: number } | null;
  streak_count?: number;
  streak_freeze_count?: number;
  freeze_capacity?: number;
  last_active_date?: string;
  at_risk?: boolean;
  longest_streak?: number;
  total_sessions?: number;
  /** True only while the window to buy a broken streak back is still open. */
  repair_available?: boolean;
  repair_eligible_until?: string | null;
  /** Sparks the repair would cost, scaled to how long the streak was. */
  repair_cost?: number;
  streak_tier?: { name?: string; freeze_cap?: number; [k: string]: unknown };
  [k: string]: unknown;
};

export async function getStreak(): Promise<Streak> {
  return apiFetch<Streak>("/api/v1/streak");
}

/**
 * One item in the Sparks shop.
 *
 * The catalogue is not fetched separately — `/study/points` already
 * returns it under `shop.items`, keyed by id, along with the week's
 * discounted item. Hard-coding a copy in the app would drift the moment a
 * price changed on the server.
 */
export type ShopItem = {
  name: string;
  price: number;
  kind: "protection" | "booster" | "inventory" | "cosmetic" | string;
  description?: string;
  slot?: string;
  value?: number | string;
  multiplier?: number;
  [k: string]: unknown;
};

export type Shop = {
  items?: Record<string, ShopItem>;
  weekly_deal?: { item_id?: string; discount_percent?: number; price?: number };
};

export type Purchase = {
  status?: string;
  message?: string;
  item_id?: string;
  price?: number;
  spark_balance?: number;
  streak_freeze_count?: number;
  badges_unlocked?: string[];
};

export async function buyShopItem(itemId: string): Promise<Purchase> {
  const d = await apiFetch<Purchase>("/study/shop/buy", {
    method: "POST",
    body: JSON.stringify({ item_id: itemId }),
  });
  // The catch-all branch answers HTTP 200 with status "error", so a
  // purchase that failed would otherwise look like one that worked — and
  // the app would show a balance it never actually spent.
  if (d?.status === "error") throw new ApiError(400, String(d.message || "Couldn't buy that."));
  return d;
}

export type Repair = {
  status?: string;
  message?: string;
  streak_count?: number;
  spark_balance?: number;
  repair_cost?: number;
  used_repair_credit?: boolean;
};

/**
 * Buy back a streak that broke.
 *
 * Only inside the window the server opened (`repair_eligible_until`), once
 * every 30 days, and it costs Sparks scaled to how long the streak was.
 */
export async function repairStreak(): Promise<Repair> {
  const d = await apiFetch<Repair>("/study/repair", {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (d?.status === "error") {
    throw new ApiError(400, String(d.message || "Couldn't repair that streak."));
  }
  return d;
}

/* ── Study tools ──────────────────────────────────────────────────── */

/**
 * One pipeline, three ways in.
 *
 * Pasting text, a PDF, and a YouTube link all end at the same place: a
 * string of study material handed to `/study/generate`, which returns the
 * key concepts (the flashcards) and the questions (the quiz). The two
 * extractors exist only to produce that string, and both answer in the
 * same `{status, text}` shape.
 */
export type StudyCard = { term: string; definition: string };

export type StudyQuestion = {
  id: number;
  type?: string;
  question: string;
  answer: string;
  hint?: string;
};

export type StudySet = {
  title?: string;
  key_concepts?: StudyCard[];
  questions?: StudyQuestion[];
};

/** These endpoints report failure as `{status: "error"}` over HTTP 200 in
 *  several branches, so the status is checked rather than the response code. */
function studyOk<T extends { status?: string; message?: string }>(d: T, fallback: string): T {
  if (!d || d.status === "error") {
    throw new ApiError(400, String(d?.message || fallback));
  }
  return d;
}

export async function generateStudySet(
  content: string,
  numQuestions = 8,
): Promise<StudySet> {
  const d = await apiFetch<{ status?: string; message?: string; data?: StudySet }>(
    "/study/generate",
    { method: "POST", body: JSON.stringify({ content, num_questions: numQuestions }) },
  );
  return studyOk(d, "Couldn't build a study set from that.").data || {};
}

export async function extractPdfText(file: {
  uri: string;
  name?: string;
  mimeType?: string;
}): Promise<string> {
  const form = new FormData();
  // React Native's FormData takes this {uri, name, type} shape rather than a
  // Blob; the field name has to be `file`, which is what the endpoint reads.
  form.append("file", {
    uri: file.uri,
    name: file.name || "notes.pdf",
    type: file.mimeType || "application/pdf",
  } as unknown as Blob);
  const d = await apiUpload<{ status?: string; message?: string; text?: string }>(
    "/study/extract-pdf",
    form,
  );
  return studyOk(d, "Couldn't read that PDF.").text || "";
}

export async function extractYoutubeText(url: string): Promise<string> {
  const d = await apiFetch<{ status?: string; message?: string; text?: string }>(
    "/study/youtube",
    { method: "POST", body: JSON.stringify({ url }) },
  );
  return studyOk(d, "Couldn't fetch that video's transcript.").text || "";
}

export type Evaluation = {
  verdict?: "correct" | "partial" | "incorrect";
  score?: number;
  what_was_right?: string;
  what_was_missing?: string;
  critique?: string;
  memory_anchor?: string;
  better_answer?: string;
  points_earned?: number;
};

/** Marks a typed answer against the expected one, semantically rather than
 *  by string match — so "cells make energy" counts for a mitochondria
 *  question. */
export async function evaluateAnswer(args: {
  question: string;
  correctAnswer: string;
  userAnswer: string;
  confidence?: "low" | "medium" | "high";
}): Promise<Evaluation> {
  const d = await apiFetch<{ status?: string; message?: string; evaluation?: Evaluation }>(
    "/study/evaluate",
    {
      method: "POST",
      body: JSON.stringify({
        question: args.question,
        correct_answer: args.correctAnswer,
        user_answer: args.userAnswer,
        confidence: args.confidence || "medium",
      }),
    },
  );
  return studyOk(d, "Couldn't mark that answer.").evaluation || {};
}

/* ── Learning profile ─────────────────────────────────────────────── */

export type Identity = {
  grade_level?: string | null;
  focus_areas?: string[] | string | null;
  goals?: string | null;
  weekly_commitments?: string | null;
  availability?: Record<string, unknown> | string | null;
  [k: string]: unknown;
};

export async function getIdentity(): Promise<Identity> {
  return apiFetch<Identity>("/api/v1/identity");
}

export async function patchIdentity(patch: Identity): Promise<Identity> {
  const d = await apiFetch<{ identity: Identity }>("/api/v1/identity", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return d?.identity || {};
}

/* ── Plani (AI tutor) ─────────────────────────────────────────────── */

export type ChatMessage = { role: "user" | "assistant"; content: string };

export type TutorReply = {
  reply: string;
  conversation_id?: number;
  title?: string;
  refusal?: unknown;
};

export async function askPlani(
  messages: ChatMessage[],
  conversationId?: number | null,
): Promise<TutorReply> {
  return apiFetch<TutorReply>("/api/tutor", {
    method: "POST",
    body: JSON.stringify({
      messages,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }),
  });
}

export type Conversation = { id: number; title: string; updated_at?: string };

export async function getConversations(): Promise<Conversation[]> {
  const d = await apiFetch<{ conversations?: Conversation[] } | Conversation[]>(
    "/api/tutor/conversations",
  );
  if (Array.isArray(d)) return d;
  return d?.conversations || [];
}

export async function getConversation(
  id: number,
): Promise<{ id: number; title: string; messages: ChatMessage[] }> {
  const d = await apiFetch<{ id: number; title: string; messages?: unknown[] }>(
    `/api/tutor/conversations/${id}`,
  );
  // The stored history includes system-side entries and image markers that
  // are not chat turns; anything without a role we recognise is dropped
  // rather than rendered as a blank bubble.
  const messages = (d?.messages || [])
    .filter(
      (m): m is ChatMessage =>
        !!m &&
        typeof m === "object" &&
        ((m as ChatMessage).role === "user" || (m as ChatMessage).role === "assistant") &&
        typeof (m as ChatMessage).content === "string",
    )
    .map((m) => ({ role: m.role, content: m.content }));
  return { id: d.id, title: d.title, messages };
}

export async function deleteConversation(id: number): Promise<void> {
  await apiFetch(`/api/tutor/conversations/${id}`, { method: "DELETE" });
}

/* ── Push ─────────────────────────────────────────────────────────── */

export async function registerPushToken(token: string): Promise<void> {
  await apiFetch("/api/v1/push/register", { method: "POST", body: JSON.stringify({ token }) });
}

export async function unregisterPushToken(token: string): Promise<void> {
  await apiFetch("/api/v1/push/unregister", { method: "POST", body: JSON.stringify({ token }) });
}

/* ── Health ───────────────────────────────────────────────────────── */

export async function apiHealth(): Promise<{ status: string; version: string }> {
  return apiFetch("/api/v1/health");
}

/* ── Snap & Solve (Plani vision) ──────────────────────────────────── */

/**
 * Send a photo of a problem to Plani.
 *
 * `mode: "multi"` tells the server the image is a whole worksheet and every
 * visible problem should be worked, which is a different prompt and a much
 * larger token budget — worth asking for explicitly rather than letting the
 * model guess from the picture.
 */
export async function askPlaniVision(args: {
  imageBase64: string;
  mime?: string;
  question?: string;
  subject?: string;
  mode?: "single" | "multi";
  conversationId?: number | null;
}): Promise<TutorReply> {
  return apiFetch<TutorReply>("/api/tutor/vision", {
    method: "POST",
    body: JSON.stringify({
      image_b64: args.imageBase64,
      image_mime: args.mime || "image/jpeg",
      question: args.question || "",
      subject: args.subject || "General",
      mode: args.mode || "single",
      ...(args.conversationId ? { conversation_id: args.conversationId } : {}),
    }),
  });
}

/* ── Focus sessions (Active) ──────────────────────────────────────── */

export type FocusSession = {
  id: number;
  block_id?: string | null;
  task_id?: string | null;
  title: string;
  course?: string;
  kind?: string;
  difficulty?: string;
  planned_minutes: number;
  due_date?: string | null;
  state: "running" | "paused" | "finished" | "abandoned" | string;
  started_at?: string | null;
  ended_at?: string | null;
  active_seconds: number;
  paused_seconds: number;
  pause_count: number;
  completed_work: boolean;
  progress_percent: number;
  perceived_difficulty?: number | null;
  focus?: {
    enabled: boolean;
    samples: number;
    ratio: number | null;
    distraction_events: number;
    longest_streak_seconds: number;
  };
};

export type FocusStats = {
  sessions: number;
  completed: number;
  completion_rate: number | null;
  total_minutes: number;
  median_minutes: number | null;
  mean_focus_ratio: number | null;
};

/**
 * The session already in progress, if any.
 *
 * Called on launch so closing the app mid-session and reopening it picks
 * the timer back up rather than silently losing the work — the server is
 * the clock of record, not the phone.
 */
export async function getCurrentSession(): Promise<FocusSession | null> {
  const d = await apiFetch<{ session: FocusSession | null }>("/api/active/current");
  return d?.session ?? null;
}

export async function startSession(args: {
  title: string;
  plannedMinutes: number;
  course?: string;
  taskId?: string;
  blockId?: string;
  dueDate?: string | null;
  kind?: string;
  difficulty?: string;
}): Promise<FocusSession> {
  const d = await apiFetch<{ session: FocusSession }>("/api/active/start", {
    method: "POST",
    body: JSON.stringify({
      title: args.title,
      planned_minutes: args.plannedMinutes,
      course: args.course || "",
      task_id: args.taskId,
      block_id: args.blockId,
      due_date: args.dueDate,
      kind: args.kind || "homework",
      difficulty: args.difficulty || "medium",
      // Attention tracking is a webcam feature on the desktop site. There
      // is no phone equivalent, so the session is started without it
      // rather than sending focus samples the app cannot measure.
      focus_enabled: false,
    }),
  });
  return d.session;
}

/**
 * Push elapsed time to the server.
 *
 * `active_seconds` is a running total rather than a delta, so a heartbeat
 * lost to a dead spot costs nothing — the next one carries the true
 * figure. The server clamps it against wall-clock time, so a phone whose
 * clock is wrong cannot inflate a session.
 */
export async function heartbeatSession(
  id: number,
  args: { activeSeconds: number; pausedSeconds: number; pauseCount: number; state: "running" | "paused" },
): Promise<FocusSession> {
  const d = await apiFetch<{ session: FocusSession }>(`/api/active/${id}/heartbeat`, {
    method: "POST",
    body: JSON.stringify({
      active_seconds: Math.round(args.activeSeconds),
      paused_seconds: Math.round(args.pausedSeconds),
      pause_count: args.pauseCount,
      state: args.state,
    }),
  });
  return d.session;
}

export async function finishSession(
  id: number,
  args: {
    completed: boolean;
    activeSeconds: number;
    progressPercent?: number;
    perceivedDifficulty?: number | null;
    notes?: string;
  },
): Promise<{ session: FocusSession; learned?: unknown }> {
  return apiFetch(`/api/active/${id}/finish`, {
    method: "POST",
    body: JSON.stringify({
      completed: args.completed,
      active_seconds: Math.round(args.activeSeconds),
      progress_percent: args.progressPercent,
      perceived_difficulty: args.perceivedDifficulty,
      notes: args.notes || "",
    }),
  });
}

export async function getFocusStats(): Promise<{ stats: FocusStats; recent: FocusSession[] }> {
  return apiFetch("/api/active/stats");
}

/* ── Connected school platforms ───────────────────────────────────── */

export type Provider = {
  key: string;
  display_name: string;
  docs_url?: string;
  configured: boolean;
  connected?: boolean;
  last_synced_at?: string | null;
  last_sync_count?: number;
};

export async function getProviders(): Promise<Provider[]> {
  const d = await apiFetch<{ providers: Provider[] }>("/api/lms/status");
  return d?.providers || [];
}

export async function syncProvider(key: string): Promise<{ synced: number }> {
  return apiFetch(`/api/lms/${encodeURIComponent(key)}/sync`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function disconnectProvider(key: string): Promise<void> {
  await apiFetch(`/api/lms/${encodeURIComponent(key)}/disconnect`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export type GoogleCalendarAccount = {
  id: number;
  email?: string;
  name?: string;
  is_active?: boolean;
};

export type GoogleCalendarStatus = {
  connected: boolean;
  accounts: GoogleCalendarAccount[];
  active_id?: number | null;
  active_email?: string;
};

export async function getGoogleCalendarStatus(): Promise<GoogleCalendarStatus> {
  return apiFetch<GoogleCalendarStatus>("/gcal/status");
}

export async function disconnectGoogleCalendar(): Promise<void> {
  await apiFetch("/oauth/google/disconnect", { method: "POST", body: JSON.stringify({}) });
}

/** Make one of several connected Google accounts the one that counts. */
export async function activateGoogleAccount(id: number): Promise<void> {
  await apiFetch("/gcal/activate", { method: "POST", body: JSON.stringify({ id }) });
}

/** Drop a single Google account, leaving any others connected. */
export async function removeGoogleAccount(id: number): Promise<void> {
  await apiFetch("/gcal/remove", { method: "POST", body: JSON.stringify({ id }) });
}

/* ── School profiles ──────────────────────────────────────────────── */

/**
 * A student can have more than one school account linked — a district
 * StudentVue and a college Canvas, or two Canvas instances. Exactly one is
 * active at a time, and it decides whose assignments every other endpoint
 * answers with. Switching is therefore not a cosmetic setting: it changes
 * what the whole app is about.
 */
export type SchoolProfile = {
  id: string;
  name: string;
  login_type: string;
  is_active: boolean;
};

export type SchoolProfiles = {
  is_guest: boolean;
  email?: string;
  profiles: SchoolProfile[];
  active: string | null;
};

/**
 * These four endpoints answer `{"status": "error"}` with HTTP 200, so a
 * plain `apiFetch` would report success on every failure — a rename that
 * silently did nothing, a switch that left the old profile active. Each
 * one is checked here rather than at four call sites.
 */
async function profileWrite(path: string, body: Record<string, unknown>, failure: string) {
  const d = await apiFetch<{ status?: string; message?: string }>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (d?.status && d.status !== "ok") throw new ApiError(400, d.message || failure);
}

export async function getSchoolProfiles(): Promise<SchoolProfiles> {
  const d = await apiFetch<SchoolProfiles>("/profiles/list");
  return { is_guest: !!d?.is_guest, profiles: d?.profiles || [], active: d?.active ?? null, email: d?.email };
}

export async function switchSchoolProfile(id: string): Promise<void> {
  await profileWrite("/profiles/switch", { id }, "Couldn't switch profile.");
}

export async function renameSchoolProfile(id: string, name: string): Promise<void> {
  await profileWrite("/profiles/rename", { id, name }, "Couldn't rename that profile.");
}

export async function removeSchoolProfile(id: string): Promise<void> {
  await profileWrite("/profiles/delete", { id }, "Couldn't remove that profile.");
}

/* ── Quests ───────────────────────────────────────────────────────── */

export type Quest = {
  id?: string | number;
  key?: string;
  title?: string;
  label?: string;
  description?: string;
  progress?: number;
  target?: number;
  goal?: number;
  reward?: number;
  claimed?: boolean;
  complete?: boolean;
  [k: string]: unknown;
};

export async function getQuests(): Promise<Quest[]> {
  const d = await apiFetch<{ quests?: Quest[] } | Quest[]>("/study/quests");
  if (Array.isArray(d)) return d;
  return d?.quests || [];
}

/* ── Learning graph ───────────────────────────────────────────────── */

export type Concept = {
  subject: string;
  topic: string;
  concept: string;
  mastery_score: number;
  confidence_score: number;
  times_seen: number;
  accuracy: number;
  days_since_review: number;
  /** 0..1 — how likely this has slipped since it was last looked at. */
  forgetting_risk: number;
};

export type SubjectMastery = {
  subject: string;
  avg_mastery: number;
  concept_count: number;
  weakest_concept: string | null;
  strongest_concept: string | null;
};

export type LearningDashboard = {
  profile?: {
    learning_pace?: string;
    strongest_subjects?: string[];
    weakest_subjects?: string[];
    retention_score?: number;
    engagement_score?: number;
    gpa_snapshot?: number | null;
  };
  strongest_concepts?: Concept[];
  weakest_concepts?: Concept[];
  /** Ordered by forgetting risk — what to review before it goes. */
  forgetting_soon?: Concept[];
  mastery_by_subject?: SubjectMastery[];
  study_trend?: number[];
  retention_trend?: number[];
  event_count_7d?: number;
};

export async function getLearningDashboard(): Promise<LearningDashboard> {
  return apiFetch<LearningDashboard>("/api/learning/dashboard");
}

/* ── Connecting an account from the phone ─────────────────────────── */

export type LinkSession = {
  /** Open this in the system browser; it arrives already signed in. */
  url: string;
  expires_in: number;
  provider: string;
  /** The redirect that means "done" — watch for it to close the browser. */
  return_url: string;
};

/**
 * Mint a one-time URL that opens the browser already signed in as you.
 *
 * Connecting Canvas or Google cannot happen inside the app: both flows
 * start at a Flask route that reads the session cookie rather than the
 * bearer token, and Google refuses to run OAuth in an embedded web view
 * at all — hardest of all against the supervised accounts these students
 * often have. Without this the browser opens logged out and the
 * connection attaches to nobody.
 */
export async function startLinkSession(provider: string): Promise<LinkSession> {
  return apiFetch<LinkSession>("/api/v1/link/session", {
    method: "POST",
    body: JSON.stringify({ provider }),
  });
}

/* ── Google Calendar ──────────────────────────────────────────────── */

export type CalendarEvent = {
  summary?: string;
  start?: string;
  end?: string;
  id?: string;
  [k: string]: unknown;
};

export async function getCalendarEvents(): Promise<{ connected: boolean; events: CalendarEvent[] }> {
  const d = await apiFetch<{ connected?: boolean; events?: CalendarEvent[] }>("/calendar/events");
  return { connected: !!d?.connected, events: d?.events || [] };
}

/**
 * Push the study plan into Google Calendar.
 *
 * `skipOverlaps` defaults on: the plan is generated around the student's
 * free time, but their calendar moves after the plan is built, and
 * double-booking someone onto an event they already accepted is worse
 * than skipping a block.
 */
export async function exportPlanToCalendar(
  scheduleData: unknown,
  skipOverlaps = true,
): Promise<{ status: string; created?: number; skipped?: number; message?: string }> {
  return apiFetch("/calendar/export", {
    method: "POST",
    body: JSON.stringify({ schedule_data: scheduleData, skip_overlaps: skipOverlaps }),
  });
}

/* ── Manual scheduler ─────────────────────────────────────────────── */

export type ManualBlock = {
  /** "HH:MM", 24-hour. */
  start: string;
  end: string;
  label?: string;
  kind?: string;
  [k: string]: unknown;
};

export type ManualPreset = {
  id: number;
  name: string;
  blocks: ManualBlock[];
  [k: string]: unknown;
};

export async function getPresets(): Promise<ManualPreset[]> {
  const d = await apiFetch<{ presets?: ManualPreset[] }>("/api/scheduler/manual/presets");
  return d?.presets || [];
}

export async function savePreset(name: string, blocks: ManualBlock[]): Promise<ManualPreset> {
  const d = await apiFetch<{ preset: ManualPreset }>("/api/scheduler/manual/presets", {
    method: "POST",
    body: JSON.stringify({ name, blocks }),
  });
  return d.preset;
}

export async function deletePreset(id: number): Promise<void> {
  await apiFetch(`/api/scheduler/manual/presets/${id}`, { method: "DELETE" });
}

/**
 * Turn hand-placed blocks into a plan the rest of the app renders.
 *
 * `presetId` applies a saved routine to every date given, which is the
 * common case — one routine, five weekdays.
 */
export async function buildManualPlan(args: {
  days: { date: string; blocks?: ManualBlock[] }[];
  presetId?: number;
  name?: string;
  save?: boolean;
}): Promise<unknown> {
  return apiFetch("/api/scheduler/manual/build", {
    method: "POST",
    body: JSON.stringify({
      days: args.days,
      ...(args.presetId ? { preset_id: args.presetId } : {}),
      ...(args.name ? { name: args.name } : {}),
      ...(args.save ? { save: true } : {}),
    }),
  });
}

/* ── Editing a manual task ────────────────────────────────────────── */

export async function updateManualTask(
  id: number | string,
  patch: Partial<NewTask>,
): Promise<void> {
  await apiFetch("/tasks/manual/update", {
    method: "POST",
    body: JSON.stringify({ id, ...patch }),
  });
}

export async function deleteManualTask(id: number | string): Promise<void> {
  await apiFetch("/tasks/manual/delete", { method: "POST", body: JSON.stringify({ id }) });
}
