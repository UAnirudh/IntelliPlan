import * as Notifications from "expo-notifications";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Task } from "./api";
import { parseDate } from "./format";

/**
 * On-device deadline reminders.
 *
 * Separate from `push.ts`, and not a fallback for it. Remote push needs a
 * server that knows what is due and a token it can reach — that is what
 * push.ts registers, and it does not work in Expo Go at all. These are
 * scheduled by the phone against work the phone has already fetched, so
 * they fire in Expo Go, they fire in airplane mode, and they cost nothing
 * to deliver. The trade is that they only know about deadlines as of the
 * last time the app was opened.
 *
 * Both can be on. A student with a dev build gets server nudges about
 * things that changed while the app was closed, and these for what it
 * already knew.
 */

const ENABLED_KEY = "ip_local_reminders";
/** Marks our own notifications so we never cancel somebody else's. */
const TAG = "ip-deadline";

/**
 * iOS silently drops scheduled notifications past 64 pending, and the
 * ones it keeps are not necessarily the soonest. Scheduling only the
 * nearest deadlines keeps the cap from choosing for us.
 */
const MAX_SCHEDULED = 32;

/** How far out to bother reminding. Beyond this it is noise, not a nudge. */
const HORIZON_DAYS = 7;

/** Evening before, local time — late enough to be actionable tonight. */
const EVENING_HOUR = 18;

export async function areRemindersEnabled(): Promise<boolean> {
  return (await AsyncStorage.getItem(ENABLED_KEY)) === "1";
}

export async function setRemindersEnabled(on: boolean): Promise<boolean> {
  if (!on) {
    await AsyncStorage.setItem(ENABLED_KEY, "0");
    await cancelAll();
    return false;
  }

  const existing = await Notifications.getPermissionsAsync();
  let status = existing.status;
  if (status !== "granted") status = (await Notifications.requestPermissionsAsync()).status;
  if (status !== "granted") {
    await AsyncStorage.setItem(ENABLED_KEY, "0");
    return false;
  }

  await AsyncStorage.setItem(ENABLED_KEY, "1");
  return true;
}

async function cancelAll(): Promise<void> {
  const scheduled = await Notifications.getAllScheduledNotificationsAsync();
  await Promise.all(
    scheduled
      .filter((n) => (n.content.data as { tag?: string } | null)?.tag === TAG)
      .map((n) => Notifications.cancelScheduledNotificationAsync(n.identifier)),
  );
}

/** When to fire for a given deadline, or null if there is no useful moment. */
function fireAt(due: Date, now: Date): Date | null {
  const evening = new Date(due);
  evening.setDate(evening.getDate() - 1);
  evening.setHours(EVENING_HOUR, 0, 0, 0);
  if (evening > now) return evening;

  // Due today, or the evening before has already passed: fall back to two
  // hours ahead of the deadline itself. A reminder that fires after the
  // thing was due is not a reminder, it is a rebuke.
  const twoHoursBefore = new Date(due.getTime() - 2 * 60 * 60 * 1000);
  if (twoHoursBefore > now) return twoHoursBefore;

  return null;
}

/**
 * Rebuild the schedule from the current task list.
 *
 * Cancel-then-reschedule rather than diffing: the list is small, the
 * operation is idempotent, and a diff would have to track which
 * notification belonged to which task across renames and platform
 * re-syncs — three ways to end up reminding someone about work they
 * finished last week.
 */
export async function syncReminders(tasks: Task[]): Promise<number> {
  if (!(await areRemindersEnabled())) return 0;

  await cancelAll();

  const now = new Date();
  const horizon = new Date(now.getTime() + HORIZON_DAYS * 86_400_000);

  const upcoming = tasks
    .filter((t) => !t.dismissed)
    .map((t) => ({ task: t, due: parseDate(t.due_date) }))
    .filter((x): x is { task: Task; due: Date } => !!x.due && x.due > now && x.due <= horizon)
    .sort((a, b) => a.due.getTime() - b.due.getTime())
    .slice(0, MAX_SCHEDULED);

  let scheduled = 0;
  for (const { task, due } of upcoming) {
    const at = fireAt(due, now);
    if (!at) continue;
    try {
      await Notifications.scheduleNotificationAsync({
        content: {
          title: task.course ? `${task.course} — due soon` : "Due soon",
          body: task.title,
          data: { tag: TAG, title: task.title },
        },
        trigger: {
          type: Notifications.SchedulableTriggerInputTypes.DATE,
          date: at,
        },
      });
      scheduled += 1;
    } catch {
      // One deadline that will not schedule should not cost the student
      // the other thirty-one.
    }
  }
  return scheduled;
}
