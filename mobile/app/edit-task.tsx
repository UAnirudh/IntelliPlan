import React, { useMemo, useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { deleteManualTask, NewTask, updateManualTask } from "../lib/api";
import { enqueue, isRetryable } from "../lib/queue";
import { useConfirm } from "../components/Confirm";
import { useTheme } from "../theme/ThemeProvider";
import { space } from "../theme/tokens";
import { shortDate } from "../lib/format";
import { Button, Field, Label, Notice, Screen, SegmentedRow, T } from "../components/ui";

/**
 * Changing a task you typed yourself.
 *
 * Only manual tasks can be edited, and that is not a limitation worth
 * hiding: an assignment from Canvas is a copy of something the teacher
 * owns, so letting a student rewrite its title here would only make their
 * list disagree with their teacher's at the next sync. `task.tsx`
 * therefore offers this screen when — and only when — `source` is
 * `manual` and the row carries the row id the update endpoint needs.
 */

const PRIORITIES = [
  { label: "Low", value: "Low" },
  { label: "Medium", value: "Medium" },
  { label: "High", value: "High" },
];

const ESTIMATES = [
  { label: "15m", value: 15 },
  { label: "30m", value: 30 },
  { label: "1h", value: 60 },
  { label: "2h", value: 120 },
];

/**
 * Due dates, offered relative to today — plus "Keep".
 *
 * A stored due date is an arbitrary string set at any point in the past,
 * so unlike the new-task screen this row cannot start on one of the fixed
 * options without silently moving the deadline the moment someone opens
 * the screen to fix a typo. `Keep` is the default and sends no due_date at
 * all, which the update endpoint reads as "leave it alone".
 */
const KEEP = -1;
const WHEN = [
  { label: "Keep", value: KEEP },
  { label: "Today", value: 0 },
  { label: "Tomorrow", value: 1 },
  { label: "In 3 days", value: 3 },
  { label: "Next week", value: 7 },
];

function isoIn(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(23, 59, 0, 0);
  return d.toISOString();
}

type TaskParam = {
  id?: number | string;
  title?: string;
  course?: string;
  due_date?: string;
  priority?: string;
  estimated_time?: number;
  est_minutes?: number;
  notes?: string;
  source?: string;
};

/** The estimate rounded to whichever offered option it is nearest, so a
 *  45-minute task opens with something selected rather than nothing. */
function nearestEstimate(mins: number | undefined): number {
  if (!mins) return 60;
  return ESTIMATES.reduce((best, o) =>
    Math.abs(o.value - mins) < Math.abs(best - mins) ? o.value : best,
    ESTIMATES[0].value,
  );
}

export default function EditTaskScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const confirm = useConfirm();
  const { data } = useLocalSearchParams<{ data?: string }>();

  const task = useMemo<TaskParam | null>(() => {
    if (!data) return null;
    try {
      return JSON.parse(data) as TaskParam;
    } catch {
      return null;
    }
  }, [data]);

  const [title, setTitle] = useState(task?.title || "");
  const [course, setCourse] = useState(task?.course || "");
  const [notes, setNotes] = useState(task?.notes || "");
  const [priority, setPriority] = useState(task?.priority || "Medium");
  const [estimate, setEstimate] = useState(
    nearestEstimate(task?.estimated_time ?? task?.est_minutes),
  );
  const [due, setDue] = useState(KEEP);
  const [busy, setBusy] = useState<"save" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const id = task?.id;

  /**
   * Leave, all the way out.
   *
   * Not `back()`: behind this modal is the detail screen, still rendering
   * the copy of the task it was handed on the way in. Returning there
   * after a rename shows the old title, and after a delete shows buttons
   * that can only 404. The lists underneath refetch when they regain
   * focus, so dismissing every modal is what actually shows the truth —
   * and it lands on whichever tab the student started from.
   */
  function done() {
    if (router.dismissAll) router.dismissAll();
    else router.back();
  }

  if (!task || id === undefined || id === null || id === "") {
    return (
      <Screen>
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: space.xl }}>
          <T tone="muted" style={{ textAlign: "center" }}>
            That task can't be edited here.
          </T>
        </View>
      </Screen>
    );
  }

  async function save() {
    if (!title.trim()) {
      setError("Give the task a title.");
      return;
    }
    setBusy("save");
    setError(null);
    const patch: Partial<NewTask> = {
      title: title.trim(),
      course: course.trim() || "Personal",
      priority,
      estimated_time: estimate,
      notes: notes.trim(),
      ...(due === KEEP ? {} : { due_date: isoIn(due) }),
    };
    try {
      await updateManualTask(id!, patch);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      done();
    } catch (e: any) {
      if (isRetryable(e)) {
        await enqueue({ kind: "updateTask", taskId: id!, patch });
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
        done();
        return;
      }
      setError(e?.message || "Couldn't save that. Try again.");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    // Deleting is the one thing here that cannot be undone from the app —
    // there is no trash on the server to fish it back out of.
    const choice = await confirm({
      title: "Delete this task?",
      message: `"${task!.title || "This task"}" will be gone for good.`,
      actions: [
        { label: "Delete", destructive: true },
        { label: "Cancel", cancel: true },
      ],
    });
    if (choice !== 0) return;

    setBusy("delete");
    setError(null);
    try {
      await deleteManualTask(id!);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      done();
    } catch (e: any) {
      if (isRetryable(e)) {
        await enqueue({ kind: "deleteTask", taskId: id! });
        done();
        return;
      }
      setError(e?.message || "Couldn't delete that. Try again.");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setBusy(null);
    }
  }

  const currentDue = shortDate(task.due_date);

  return (
    <Screen>
      <View
        style={{
          flexDirection: "row",
          alignItems: "center",
          gap: space.md,
          paddingTop: insets.top + space.sm,
          paddingHorizontal: space.lg,
          paddingBottom: space.md,
          borderBottomWidth: 1,
          borderBottomColor: colors.border,
        }}
      >
        <T variant="lg" weight="700" style={{ flex: 1 }}>
          Edit task
        </T>
        <Pressable onPress={() => router.back()} hitSlop={10} accessibilityRole="button" accessibilityLabel="Close">
          <Ionicons name="close" size={26} color={colors.textSecondary} />
        </Pressable>
      </View>

      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView
          contentContainerStyle={{ padding: space.lg, gap: space.lg, paddingBottom: insets.bottom + 40 }}
          keyboardShouldPersistTaps="handled"
        >
          <Field
            label="What needs doing"
            placeholder="Finish lab write-up"
            value={title}
            onChangeText={setTitle}
            returnKeyType="next"
          />

          <Field
            label="Course"
            placeholder="Biology (optional)"
            value={course}
            onChangeText={setCourse}
            returnKeyType="next"
          />

          <View style={{ gap: space.sm }}>
            <Label>Due</Label>
            <SegmentedRow options={WHEN} value={due} onChange={setDue} />
            {due === KEEP ? (
              <T variant="xs" tone="muted">
                {currentDue ? `Staying on ${currentDue}.` : "No due date set."}
              </T>
            ) : null}
          </View>

          <View style={{ gap: space.sm }}>
            <Label>Priority</Label>
            <SegmentedRow options={PRIORITIES} value={priority} onChange={setPriority} />
          </View>

          <View style={{ gap: space.sm }}>
            <Label>How long it'll take</Label>
            <SegmentedRow options={ESTIMATES} value={estimate} onChange={setEstimate} />
          </View>

          <Field
            label="Notes"
            placeholder="Anything you want to remember (optional)"
            value={notes}
            onChangeText={setNotes}
            multiline
          />

          {error ? <Notice text={error} icon="alert-circle-outline" /> : null}

          <Button title="Save changes" icon="checkmark" busy={busy === "save"} onPress={save} />

          <Button
            title="Delete task"
            kind="danger"
            icon="trash-outline"
            busy={busy === "delete"}
            onPress={remove}
          />
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}
