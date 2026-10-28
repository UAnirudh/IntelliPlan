import React, { useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { createTask, quickAddTask } from "../lib/api";
import { refreshAndSyncReminders } from "../lib/reminders";
import { enqueue, isRetryable } from "../lib/queue";
import { useTheme } from "../theme/ThemeProvider";
import { space } from "../theme/tokens";
import { Button, Field, Label, Notice, Screen, SegmentedRow, T } from "../components/ui";

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

/** Days from today, offered as buttons — a date picker is four taps for
 *  "tomorrow", which is what most manual tasks are. */
const WHEN = [
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

export default function NewTaskScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [title, setTitle] = useState("");
  const [course, setCourse] = useState("");
  const [notes, setNotes] = useState("");
  const [priority, setPriority] = useState("Medium");
  const [estimate, setEstimate] = useState(60);
  const [due, setDue] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quick, setQuick] = useState("");
  const [quickBusy, setQuickBusy] = useState(false);
  const [quickResult, setQuickResult] = useState<string | null>(null);

  // One line, parsed by the server: "chem quiz fri 30m". The reply says
  // where the task landed in the plan, which is shown in place so the
  // student can add the next one without leaving the screen.
  async function quickAdd() {
    const text = quick.trim();
    if (!text) return;
    setQuickBusy(true);
    setError(null);
    try {
      const result = await quickAddTask(text);
      void refreshAndSyncReminders().catch(() => {});
      setQuickResult(result.message);
      setQuick("");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "Couldn't add that. Try again.");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setQuickBusy(false);
    }
  }

  async function save() {
    if (!title.trim()) {
      setError("Give the task a title.");
      return;
    }
    setBusy(true);
    setError(null);
    const task = {
      title: title.trim(),
      course: course.trim() || "Personal",
      due_date: isoIn(due),
      priority,
      estimated_time: estimate,
      notes: notes.trim(),
    };
    try {
      await createTask(task);
      void refreshAndSyncReminders().catch(() => {});
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      router.back();
    } catch (e: any) {
      if (isRetryable(e)) {
        // Someone who has just typed out a task and picked four options
        // should not lose all of it because they walked into a lift.
        await enqueue({ kind: "createTask", task });
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
        router.back();
        return;
      }
      setError(e?.message || "Couldn't save that. Try again.");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setBusy(false);
    }
  }

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
        <View style={{ flex: 1, gap: 2 }}>
          <T variant="lg" weight="700">New task</T>
          <T variant="xs" tone="muted">Capture it and plan your next step</T>
        </View>
        <Pressable
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Close new task"
          style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.bgCard, alignItems: "center", justifyContent: "center" }}
        >
          <Ionicons name="close" size={23} color={colors.textSecondary} />
        </Pressable>
      </View>

      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView
          contentContainerStyle={{ padding: space.lg, gap: space.lg, paddingBottom: insets.bottom + 40 }}
          keyboardShouldPersistTaps="handled"
        >
          <Field
            label="Quick add"
            placeholder="bio lab due fri 2h"
            value={quick}
            onChangeText={setQuick}
            returnKeyType="done"
            onSubmitEditing={quickAdd}
          />
          {quickResult ? <Notice text={quickResult} tone="accent" icon="checkmark-circle-outline" /> : null}
          {quick.trim() ? (
            <Button title="Add it" icon="flash-outline" busy={quickBusy} onPress={quickAdd} />
          ) : null}
          <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm, marginTop: space.xs }}>
            <View style={{ width: 28, height: 1, backgroundColor: colors.border }} />
            <T variant="xs" tone="muted">OR ADD DETAILS</T>
            <View style={{ flex: 1, height: 1, backgroundColor: colors.border }} />
          </View>

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
          </View>

          <View style={{ gap: space.sm }}>
            <Label>Priority</Label>
            <SegmentedRow options={PRIORITIES} value={priority} onChange={setPriority} />
          </View>

          <View style={{ gap: space.sm }}>
            <Label>How long it'll take</Label>
            <SegmentedRow options={ESTIMATES} value={estimate} onChange={setEstimate} />
            <T variant="xs" tone="muted">
              The scheduler uses this to fit the task into your day.
            </T>
          </View>

          <Field
            label="Notes"
            placeholder="Anything you want to remember (optional)"
            value={notes}
            onChangeText={setNotes}
            multiline
          />

          {error ? <Notice text={error} icon="alert-circle-outline" /> : null}

          <Button title="Add task" icon="checkmark" busy={busy} onPress={save} />
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}
