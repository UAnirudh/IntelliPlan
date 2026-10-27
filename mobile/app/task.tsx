import React, { useCallback, useMemo, useState } from "react";
import { Pressable, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { dismissTask, markTest, restoreTask, unmarkTest } from "../lib/api";
import { enqueue, isRetryable } from "../lib/queue";
import { useTheme } from "../theme/ThemeProvider";
import { priorityColour, radius, space } from "../theme/tokens";
import { dueLabel, minutes, shortDate } from "../lib/format";
import { Button, Card, Chip, Label, Notice, Screen, T } from "../components/ui";

/**
 * What the list rows can't hold.
 *
 * The whole task arrives as one JSON param rather than a dozen separate
 * ones: expo-router params are strings, and spreading a task across
 * `?title=…&course=…&est=…` means every new field is a change in two
 * places and a silent drop when one is forgotten.
 */
type TaskParam = {
  /** Present for manual tasks, which are the only ones editable here. */
  id?: number | string;
  title: string;
  course?: string;
  due_date?: string;
  priority?: string;
  est_minutes?: number;
  estimated_time?: number;
  platform?: string;
  source?: string;
  points?: number | string;
  notes?: string;
  why_now?: string;
  dismissed?: boolean;
  is_test?: boolean;
  rationale?: { key: string; weight: number; reason: string }[];
  score?: number;
};

export default function TaskScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { data } = useLocalSearchParams<{ data?: string }>();

  const task = useMemo<TaskParam | null>(() => {
    if (!data) return null;
    try {
      return JSON.parse(data) as TaskParam;
    } catch {
      return null;
    }
  }, [data]);

  const [done, setDone] = useState(!!task?.dismissed);
  const [isTest, setIsTest] = useState(!!task?.is_test);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const toggleDone = useCallback(async () => {
    if (!task) return;
    setBusy("done");
    setNote(null);
    try {
      if (done) await restoreTask(task.title);
      else await dismissTask(task.title);
      setDone(!done);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      if (isRetryable(e)) {
        await enqueue({ kind: done ? "restore" : "dismiss", title: task.title });
        setDone(!done);
        setNote("Saved on this phone — it'll sync when you're back online.");
      } else {
        setNote(e?.message || "That didn't save.");
      }
    } finally {
      setBusy(null);
    }
  }, [task, done]);

  const toggleTest = useCallback(async () => {
    if (!task) return;
    setBusy("test");
    setNote(null);
    try {
      if (isTest) await unmarkTest(task.title);
      else await markTest(task.title);
      setIsTest(!isTest);
    } catch (e: any) {
      if (isRetryable(e)) {
        await enqueue({ kind: isTest ? "unmarkTest" : "markTest", title: task.title });
        setIsTest(!isTest);
        setNote("Saved on this phone — it'll sync when you're back online.");
      } else {
        setNote(e?.message || "That didn't save.");
      }
    } finally {
      setBusy(null);
    }
  }, [task, isTest]);

  if (!task) {
    return (
      <Screen>
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: space.xl }}>
          <T tone="muted">That task couldn't be opened.</T>
        </View>
      </Screen>
    );
  }

  const est = task.est_minutes ?? task.estimated_time;
  // Only a task the student typed themselves can be edited. An assignment
  // from Canvas is a copy of the teacher's record, so a rewrite here would
  // just be undone by the next sync — offering the button would be a lie.
  const editable =
    (task.source || "").toLowerCase() === "manual" &&
    task.id !== undefined &&
    task.id !== null &&
    task.id !== "";
  const pri = priorityColour(colors, task.priority);
  const platform = task.platform || task.source;

  return (
    <Screen>
      <View
        style={{
          flexDirection: "row",
          alignItems: "center",
          paddingTop: insets.top + space.sm,
          paddingHorizontal: space.lg,
          paddingBottom: space.md,
          borderBottomWidth: 1,
          borderBottomColor: colors.border,
        }}
      >
        <View style={{ flex: 1, gap: 2 }}>
          <T variant="lg" weight="700">Task details</T>
          <T variant="xs" tone="muted">Review your next step</T>
        </View>
        <Pressable
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Close task details"
          style={{ width: 44, height: 44, borderRadius: radius.pill, backgroundColor: colors.bgCard, alignItems: "center", justifyContent: "center" }}
        >
          <Ionicons name="close" size={23} color={colors.textSecondary} />
        </Pressable>
      </View>

      <ScrollView
        contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: insets.bottom + 40 }}
      >
        <Card style={{ gap: space.md, padding: space.lg, backgroundColor: done ? colors.bgCard : colors.accentSoft, borderColor: done ? colors.border : colors.borderAccent }}>
          <View style={{ flexDirection: "row", alignItems: "flex-start", gap: space.md }}>
            <View style={{ width: 46, height: 46, borderRadius: radius.md, backgroundColor: done ? colors.bgElevated : colors.bgCard, alignItems: "center", justifyContent: "center" }}>
              <Ionicons name={done ? "checkmark-done" : "book-outline"} size={23} color={done ? colors.ok : colors.accent} />
            </View>
            <View style={{ flex: 1, minWidth: 0, gap: space.xs }}>
              {done ? <Label style={{ color: colors.ok }}>COMPLETED</Label> : task.priority ? <Label style={{ color: pri.fg }}>{String(task.priority).toUpperCase()} PRIORITY</Label> : null}
              <T variant="xl" weight="700" style={[{ lineHeight: 30 }, done ? { textDecorationLine: "line-through" } : undefined]}>
            {task.title}
              </T>
            </View>
          </View>
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
            {task.course ? <Chip label={String(task.course)} icon="book-outline" /> : null}
            <Chip label={dueLabel(task.due_date)} icon="time-outline" />
            {est ? <Chip label={minutes(est)} icon="hourglass-outline" /> : null}
            {task.priority ? <Chip label={String(task.priority)} fg={pri.fg} bg={pri.bg} /> : null}
            {platform ? <Chip label={String(platform)} icon="cloud-outline" /> : null}
            {task.points ? <Chip label={`${task.points} pts`} icon="ribbon-outline" /> : null}
            {isTest ? <Chip label="Test" icon="school-outline" fg={colors.accent} bg={colors.accentSoft} /> : null}
          </View>
          {task.due_date && shortDate(task.due_date) ? (
            <T variant="sm" tone="muted">
              Due {shortDate(task.due_date)}
            </T>
          ) : null}
        </Card>

        {task.why_now ? (
          <Card style={{ backgroundColor: colors.accentSoft, borderColor: colors.borderAccent, gap: 6 }}>
            <Label>Why this, now</Label>
            <T variant="base">{task.why_now}</T>
          </Card>
        ) : null}

        {/* The ranking is the product's actual claim, so it shows its
            working. A priority score a student can't interrogate is just
            an app being bossy. */}
        {task.rationale?.length ? (
          <Card style={{ gap: space.sm }}>
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
              <Label>What pushed it up the list</Label>
              {typeof task.score === "number" ? (
                <Chip label={`score ${Math.round(task.score)}`} />
              ) : null}
            </View>
            {task.rationale.map((r, i) => (
              <View key={`${r.key}-${i}`} style={{ gap: 4 }}>
                <View style={{ flexDirection: "row", justifyContent: "space-between", gap: space.sm }}>
                  <T variant="sm" style={{ flex: 1 }}>
                    {r.reason}
                  </T>
                  <T variant="sm" tone="muted" weight="600">
                    {r.weight > 0 ? "+" : ""}
                    {Math.round(r.weight)}
                  </T>
                </View>
                <View
                  style={{
                    height: 4,
                    borderRadius: radius.pill,
                    backgroundColor: colors.bgElevated,
                    overflow: "hidden",
                  }}
                >
                  <View
                    style={{
                      width: `${Math.min(100, Math.abs(r.weight))}%`,
                      height: "100%",
                      backgroundColor: r.weight >= 0 ? colors.accent : colors.warn,
                    }}
                  />
                </View>
              </View>
            ))}
          </Card>
        ) : null}

        {task.notes ? (
          <Card style={{ gap: 6 }}>
            <Label>Notes</Label>
            <T variant="base" tone="secondary">
              {task.notes}
            </T>
          </Card>
        ) : null}

        {note ? <Notice text={note} icon="alert-circle-outline" /> : null}

        <View style={{ gap: space.sm, marginTop: space.sm }}>
          {!done ? (
            <Button
              title="Focus on this"
              icon="play"
              onPress={() =>
                router.replace({
                  pathname: "/focus",
                  params: {
                    title: task.title,
                    course: task.course || "",
                    taskId: String(task.title),
                    dueDate: task.due_date || "",
                    minutes: String(Math.min(90, Math.max(15, Math.round(est || 25)))),
                  },
                })
              }
            />
          ) : null}

          <Button
            title={done ? "Put it back" : "Mark done"}
            kind="secondary"
            icon={done ? "arrow-undo" : "checkmark"}
            busy={busy === "done"}
            onPress={toggleDone}
          />

          {editable ? (
            <Button
              title="Edit task"
              kind="secondary"
              icon="create-outline"
              onPress={() =>
                router.push({ pathname: "/edit-task", params: { data: JSON.stringify(task) } })
              }
            />
          ) : null}

          <Button
            title={isTest ? "Not a test" : "This is a test"}
            kind="secondary"
            icon="school-outline"
            busy={busy === "test"}
            onPress={toggleTest}
          />
          <T variant="xs" tone="muted" style={{ textAlign: "center" }}>
            Marking a test tells the scheduler to spread revision across the days
            before it, instead of treating it as one sitting.
          </T>
        </View>
      </ScrollView>
    </Screen>
  );
}
