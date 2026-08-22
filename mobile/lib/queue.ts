import AsyncStorage from "@react-native-async-storage/async-storage";
import { ApiError, createTask, dismissTask, markTest, NewTask, restoreTask, unmarkTest } from "./api";

/**
 * Writes that could not be sent, kept until they can be.
 *
 * The read side already survives a dead spot — every response is cached and
 * replayed. The write side did not: ticking an assignment off on the bus
 * failed with a buzz and no record, so the student either did it twice or
 * lost it. Reads and writes degrading differently is worse than either
 * behaviour on its own, because it makes the app's offline story
 * unpredictable.
 *
 * Only failures that say nothing about the request are queued. A 400 means
 * the server understood and refused; retrying it forever is a way to never
 * notice a bug.
 */

const KEY = "ip_write_queue";

/**
 * The work itself, without bookkeeping.
 *
 * Split out because `Omit<Union, ...>` does not distribute over a union —
 * it collapses to the keys every member shares, which here is just `kind`,
 * so `enqueue({kind: "dismiss", title})` would not type-check.
 */
export type NewWrite =
  | { kind: "dismiss"; title: string }
  | { kind: "restore"; title: string }
  | { kind: "markTest"; title: string }
  | { kind: "unmarkTest"; title: string }
  | { kind: "createTask"; task: NewTask };

export type PendingWrite = NewWrite & { id: string; at: number };

type Listener = (count: number) => void;
const listeners = new Set<Listener>();

/** Subscribe to the pending count, for the "n changes waiting" strip. */
export function onQueueChange(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function read(): Promise<PendingWrite[]> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as PendingWrite[]) : [];
  } catch {
    return [];
  }
}

async function write(items: PendingWrite[]): Promise<void> {
  try {
    await AsyncStorage.setItem(KEY, JSON.stringify(items));
  } catch {
    /* a queue we cannot persist is still useful for this session */
  }
  listeners.forEach((fn) => fn(items.length));
}

export async function pendingCount(): Promise<number> {
  return (await read()).length;
}

export async function clearQueue(): Promise<void> {
  await AsyncStorage.removeItem(KEY).catch(() => {});
  listeners.forEach((fn) => fn(0));
}

/**
 * True when the failure was the network rather than the request.
 *
 * ApiError uses status 0 for "never reached the server" — no signal, DNS,
 * a timeout. Anything with a real status came back from the server, and a
 * server that answered has an opinion worth respecting.
 */
export function isRetryable(e: unknown): boolean {
  const err = e as ApiError;
  return err?.status === 0 || err?.status === 502 || err?.status === 503 || err?.status === 504;
}

function opposes(a: PendingWrite, b: PendingWrite): boolean {
  const pairs: Record<string, string> = {
    dismiss: "restore",
    restore: "dismiss",
    markTest: "unmarkTest",
    unmarkTest: "markTest",
  };
  return (
    "title" in a &&
    "title" in b &&
    a.title === b.title &&
    pairs[a.kind] === b.kind
  );
}

export async function enqueue(item: NewWrite): Promise<void> {
  const items = await read();
  const entry: PendingWrite = {
    ...item,
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    at: Date.now(),
  };

  // Ticking something off and putting it back while offline should send
  // nothing at all, not two requests that cancel out — and definitely not
  // in an order the server might apply backwards.
  const cancelled = items.findIndex((x) => opposes(x, entry));
  if (cancelled >= 0) {
    items.splice(cancelled, 1);
    await write(items);
    return;
  }

  items.push(entry);
  await write(items);
}

async function send(item: PendingWrite): Promise<void> {
  switch (item.kind) {
    case "dismiss":
      return dismissTask(item.title);
    case "restore":
      return restoreTask(item.title);
    case "markTest":
      return markTest(item.title);
    case "unmarkTest":
      return unmarkTest(item.title);
    case "createTask":
      await createTask(item.task);
      return;
  }
}

let flushing = false;

/**
 * Send everything waiting, oldest first.
 *
 * Serial rather than parallel: these are edits to the same small set of
 * assignments, and firing a dismiss and a restore at once leaves the
 * result down to which request the server happens to finish first.
 *
 * Returns how many were sent. Stops at the first retryable failure — if
 * the network is still down, the rest will fail too, and the queue is
 * ordered.
 */
export async function flushQueue(): Promise<number> {
  if (flushing) return 0;
  flushing = true;
  try {
    let items = await read();
    let sent = 0;

    while (items.length) {
      const [next, ...rest] = items;
      try {
        await send(next);
        sent += 1;
        items = rest;
        await write(items);
      } catch (e) {
        if (isRetryable(e)) break;
        // The server answered and refused. Retrying forever would hide a
        // real bug and block everything behind it, so it is dropped —
        // the list refreshes from the server either way, so the student
        // sees the true state rather than a lie.
        items = rest;
        await write(items);
      }
    }
    return sent;
  } finally {
    flushing = false;
  }
}
