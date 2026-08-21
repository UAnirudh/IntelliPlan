import React, { useCallback, useMemo, useRef, useState } from "react";
import { RefreshControl, SectionList, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import { dismissTask, getTasks, restoreTask, Task } from "../../lib/api";
import { useQuery } from "../../lib/useQuery";
import { useTheme } from "../../theme/ThemeProvider";
import { space } from "../../theme/tokens";
import { daysAway } from "../../lib/format";
import { Button, EmptyState, ErrorState, Label, Loading, Notice, Screen, SegmentedRow, T } from "../../components/ui";
import { Header, IconButton } from "../../components/Header";
import { TaskRow } from "../../components/TaskRow";

type Filter = "all" | "overdue" | "today" | "week" | "done";

const FILTERS: { label: string; value: Filter }[] = [
  { label: "All", value: "all" },
  { label: "Overdue", value: "overdue" },
  { label: "Today", value: "today" },
  { label: "This week", value: "week" },
  { label: "Done", value: "done" },
];

/**
 * Everything due, from every platform the student connected.
 *
 * The list comes from /api/v1/tasks — the unified feed — rather than
 * /api/v1/assignments, which is the school-platform feed only. A task the
 * student added a minute ago would be missing from the list they added it
 * in otherwise.
 */
export default function TasksScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const q = useQuery<Task[]>("tasks", getTasks);
  const [filter, setFilter] = useState<Filter>("all");
  const [course, setCourse] = useState<string>("__all");
  const [busyTitle, setBusyTitle] = useState<string | null>(null);
  /** Titles ticked off in this session, so the row updates before a refetch. */
  const [locallyDone, setLocallyDone] = useState<Set<string>>(new Set());

  // Coming back from the "add task" modal should show the new task. The
  // list is small and the endpoint is cheap, so refetching on focus beats
  // threading a "something changed" flag back through the router.
  //
  // The first focus is skipped: it lands in the same tick as useQuery's own
  // initial fetch, so without the guard every first visit to this tab fires
  // two identical requests and the second one races the first.
  const firstFocus = useRef(true);
  useFocusEffect(
    useCallback(() => {
      if (firstFocus.current) {
        firstFocus.current = false;
        return;
      }
      q.refresh();
      // Intentionally not depending on `q`: it is a fresh object each
      // render, and depending on it would refetch in a loop.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []),
  );

  const tasks = useMemo(() => q.data || [], [q.data]);

  const isDone = useCallback(
    (t: Task) => !!t.dismissed || locallyDone.has(t.title),
    [locallyDone],
  );

  const courses = useMemo(() => {
    const names = new Set<string>();
    tasks.forEach((t) => t.course && names.add(String(t.course)));
    return [{ label: "Every course", value: "__all" }].concat(
      Array.from(names)
        .sort()
        .map((n) => ({ label: n, value: n })),
    );
  }, [tasks]);

  const toggle = useCallback(
    async (t: Task) => {
      const done = isDone(t);
      setBusyTitle(t.title);
      try {
        if (done) {
          await restoreTask(t.title);
          setLocallyDone((prev) => {
            const next = new Set(prev);
            next.delete(t.title);
            return next;
          });
          // The server copy still says dismissed; clear it so a row the
          // student just restored does not snap back on the next render.
          q.setData((prev) =>
            (prev || []).map((x) => (x.title === t.title ? { ...x, dismissed: false } : x)),
          );
        } else {
          await dismissTask(t.title);
          Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
          setLocallyDone((prev) => new Set(prev).add(t.title));
        }
      } catch {
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
      } finally {
        setBusyTitle(null);
      }
    },
    [isDone, q],
  );

  const sections = useMemo(() => {
    let rows = tasks;
    if (course !== "__all") rows = rows.filter((t) => String(t.course || "") === course);

    // "Done" is its own view rather than a strikethrough mixed into the
    // list: once something is ticked off it is noise, and the only reason
    // to look at it again is to undo a mistake.
    if (filter === "done") {
      const doneRows = rows.filter(isDone);
      return doneRows.length ? [{ title: "Completed", data: doneRows }] : [];
    }

    rows = rows.filter((t) => !isDone(t));
    if (filter === "overdue") rows = rows.filter((t) => (daysAway(t.due_date) ?? 99) < 0);
    if (filter === "today") rows = rows.filter((t) => daysAway(t.due_date) === 0);
    if (filter === "week") {
      rows = rows.filter((t) => {
        const d = daysAway(t.due_date);
        return d !== null && d >= 0 && d <= 7;
      });
    }

    const buckets: Record<string, Task[]> = { Overdue: [], Today: [], "This week": [], Later: [] };
    rows.forEach((t) => {
      const d = daysAway(t.due_date);
      if (d === null) buckets.Later.push(t);
      else if (d < 0) buckets.Overdue.push(t);
      else if (d === 0) buckets.Today.push(t);
      else if (d <= 7) buckets["This week"].push(t);
      else buckets.Later.push(t);
    });

    // Soonest first inside each bucket; anything without a parseable date
    // sinks to the bottom rather than sorting as the epoch.
    const bySoonest = (a: Task, b: Task) => (daysAway(a.due_date) ?? 9e9) - (daysAway(b.due_date) ?? 9e9);

    return Object.entries(buckets)
      .filter(([, data]) => data.length)
      .map(([title, data]) => ({ title, data: data.sort(bySoonest) }));
  }, [tasks, filter, course, isDone]);

  if (q.loading) {
    return (
      <Screen>
        <Header title="Due" />
        <Loading label="Pulling from your platforms…" />
      </Screen>
    );
  }

  if (q.error && !tasks.length) {
    return (
      <Screen>
        <Header title="Due" />
        <ErrorState message={q.error} onRetry={q.reload} />
      </Screen>
    );
  }

  const openCount = tasks.filter((t) => !isDone(t)).length;

  return (
    <Screen>
      <Header
        title="Due"
        subtitle={`${openCount} open item${openCount === 1 ? "" : "s"}`}
        right={<IconButton icon="add-circle-outline" label="Add a task" onPress={() => router.push("/new-task")} />}
      />

      <View style={{ paddingLeft: space.lg, paddingVertical: space.md, gap: space.sm }}>
        <SegmentedRow options={FILTERS} value={filter} onChange={setFilter} />
        {courses.length > 2 ? (
          <SegmentedRow options={courses} value={course} onChange={setCourse} />
        ) : null}
      </View>

      <SectionList
        sections={sections}
        keyExtractor={(item, i) => `${item.title}-${i}`}
        stickySectionHeadersEnabled={false}
        contentContainerStyle={{
          paddingHorizontal: space.lg,
          paddingBottom: insets.bottom + 100,
          gap: space.sm,
        }}
        refreshControl={
          <RefreshControl refreshing={q.refreshing} onRefresh={q.refresh} tintColor={colors.accent} />
        }
        ListHeaderComponent={
          q.stale ? (
            <View style={{ marginBottom: space.sm }}>
              <Notice text="Offline — showing the last list that loaded." icon="cloud-offline-outline" />
            </View>
          ) : null
        }
        renderSectionHeader={({ section }) => (
          <View style={{ paddingTop: space.md, paddingBottom: space.xs, flexDirection: "row", justifyContent: "space-between" }}>
            <Label>{section.title}</Label>
            <T variant="micro" tone="muted">
              {section.data.length}
            </T>
          </View>
        )}
        renderItem={({ item }) => (
          <TaskRow
            task={{ ...item, done: isDone(item) }}
            busy={busyTitle === item.title}
            onToggle={() => toggle(item)}
            onPress={() =>
              router.push({
                pathname: "/task",
                params: { data: JSON.stringify({ ...item, dismissed: isDone(item) }) },
              })
            }
          />
        )}
        ListEmptyComponent={
          <EmptyState
            icon={filter === "done" ? "time-outline" : "checkmark-done-outline"}
            title={filter === "done" ? "Nothing completed yet" : "Nothing here"}
            body={
              filter === "all"
                ? "Connect a platform on the website, or add something yourself."
                : "Try a wider filter."
            }
            action={
              filter === "all" ? (
                <Button title="Add a task" kind="secondary" icon="add" onPress={() => router.push("/new-task")} />
              ) : null
            }
          />
        }
      />
    </Screen>
  );
}
