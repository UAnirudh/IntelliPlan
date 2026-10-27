import React, { useCallback, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import { Ionicons } from "@expo/vector-icons";
import {
  blockMinutes,
  blockTitle,
  Block,
  dismissTask,
  getCurrentSession,
  getSchedule,
  getStreak,
  getTasks,
  getToday,
  PlannedTask,
  refreshToday,
  SavedSchedule,
  ScheduleDay,
  Task,
  TodayPayload,
} from "../../lib/api";
import { dueLabel, minutes } from "../../lib/format";
import { useQuery } from "../../lib/useQuery";
import { useAuth } from "../../lib/auth";
import { useTheme } from "../../theme/ThemeProvider";
import { radius, space } from "../../theme/tokens";
import {
  Button,
  Card,
  Chip,
  EmptyState,
  ErrorState,
  Label,
  Loading,
  Notice,
  Screen,
  T,
} from "../../components/ui";
import { Header, IconButton } from "../../components/Header";
import { ReminderOptInPrompt } from "../../components/ReminderOptInPrompt";
import { Ring } from "../../components/Ring";
import { ForecastBars } from "../../components/Bars";
import { TaskRow } from "../../components/TaskRow";

function normalizeDays(raw: SavedSchedule | null | undefined): ScheduleDay[] {
  if (!raw) return [];
  const inner: unknown =
    (raw as any).data?.schedule ??
    (raw as any).days ??
    (raw as any).schedule ??
    (raw as any).data ??
    raw;

  if (Array.isArray(inner)) {
    return inner
      .map((entry: any) => {
        if (Array.isArray(entry)) return { blocks: entry as Block[] };
        if (!entry || typeof entry !== "object") return { blocks: [] };
        return {
          day: entry.day ?? entry.day_name ?? entry.name ?? entry.label,
          date: entry.date,
          blocks: (entry.blocks ?? entry.tasks ?? entry.items ?? []) as Block[],
        };
      })
      .filter((day) => day.blocks.length);
  }

  if (inner && typeof inner === "object") {
    return Object.entries(inner as Record<string, unknown>).map(([day, value]) => ({
      day,
      blocks: Array.isArray(value) ? (value as Block[]) : [],
    }));
  }
  return [];
}

function localDateKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate(),
  ).padStart(2, "0")}`;
}

function blocksForToday(raw: SavedSchedule | null | undefined, date: Date): Block[] {
  const days = normalizeDays(raw);
  const key = localDateKey(date);
  const exactDate = days.find((day) => day.date?.slice(0, 10) === key);
  if (exactDate) return exactDate.blocks || [];

  const weekday = date.toLocaleDateString("en-US", { weekday: "long" }).toLowerCase();
  const dayName = days.find((day) => {
    const label = String(day.day || "").toLowerCase().trim();
    return label === weekday || label.startsWith(weekday.slice(0, 3));
  });
  return dayName?.blocks || [];
}

function timeForBlock(block: Block): string {
  if (block.time_slot) return String(block.time_slot);
  if (block.start && block.end) return `${block.start} – ${block.end}`;
  return String(block.start || "Flexible");
}

function taskDetails(task: Task | PlannedTask, isFallback: boolean) {
  if (isFallback) return task;
  const planned = task as PlannedTask;
  return {
    id: planned.source_ref,
    title: planned.title,
    course: planned.course,
    due_date: planned.due_date,
    est_minutes: planned.est_minutes,
    priority: planned.priority?.tier,
    source: planned.source,
    why_now: planned.why_now,
    rationale: planned.priority?.rationale,
    score: planned.priority?.score,
  };
}

function taskEstimate(task: Task | PlannedTask): number {
  return Math.min(
    60,
    Math.max(15, Math.round(Number((task as Task).estimated_time ?? (task as PlannedTask).est_minutes) || 25)),
  );
}

export default function TodayScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();

  const today = useQuery<TodayPayload>("today", getToday);
  const streak = useQuery<Awaited<ReturnType<typeof getStreak>>>("streak", getStreak);
  const schedule = useQuery<SavedSchedule>("schedule", getSchedule);
  const fallback = useQuery<Task[]>("today-fallback", getTasks, { enabled: today.missing });
  const live = useQuery<Awaited<ReturnType<typeof getCurrentSession>>>(
    "current-session",
    getCurrentSession,
  );

  const [busyTitle, setBusyTitle] = useState<string | null>(null);
  const [done, setDone] = useState<Set<string>>(new Set());
  const [rebuilding, setRebuilding] = useState(false);

  const complete = useCallback(async (title: string) => {
    setBusyTitle(title);
    try {
      await dismissTask(title);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      setDone((prev) => new Set(prev).add(title));
    } catch {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setBusyTitle(null);
    }
  }, []);

  const rebuild = useCallback(async () => {
    setRebuilding(true);
    try {
      const fresh = await refreshToday();
      today.setData(() => fresh);
      setDone(new Set());
    } catch {
      await today.refresh();
    } finally {
      setRebuilding(false);
    }
  }, [today]);

  const loading = today.loading || (today.missing && fallback.loading);
  const data = today.data;
  const greeting = data?.student?.greeting || "Hello";
  const firstName = (data?.student?.name || user?.name || user?.email?.split("@")[0] || "").split(" ")[0];
  const now = new Date();
  const dateLabel = now.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
  const todayBlocks = useMemo(() => blocksForToday(schedule.data, now), [schedule.data]);
  const scheduleProgress = schedule.data?.progress || (schedule.data as any)?.data?.progress || {};

  if (loading) {
    return (
      <Screen>
        <Header title="Today" />
        <Loading label="Building your day…" />
      </Screen>
    );
  }

  if (today.error && !today.missing && !data && !today.stale) {
    return (
      <Screen>
        <Header title="Today" />
        <ErrorState message={today.error} onRetry={today.reload} />
      </Screen>
    );
  }

  const tasks = (today.missing ? fallback.data || [] : data?.plan || []).filter(
    (task) => !done.has(task.title),
  );
  const featuredTask = tasks[0];
  const featuredWhy = featuredTask && "why_now" in featuredTask ? featuredTask.why_now : undefined;
  const laterTasks = tasks.slice(featuredTask ? 1 : 0, 8);
  const blockProgress = todayBlocks.reduce<{ done: number; total: number }>(
    (state, block) => {
      if (block.is_break || block.unplaced) return state;
      state.total += 1;
      if (block.block_id && scheduleProgress[block.block_id]?.done) state.done += 1;
      return state;
    },
    { done: 0, total: 0 },
  );
  const focusedBlock = todayBlocks.find(
    (block) => !block.is_break && !block.unplaced && !(block.block_id && scheduleProgress[block.block_id]?.done),
  );
  const focusedBlockIndex = focusedBlock ? todayBlocks.indexOf(focusedBlock) : -1;

  const openTask = (task: Task | PlannedTask) => {
    router.push({
      pathname: "/task",
      params: { data: JSON.stringify(taskDetails(task, today.missing)) },
    });
  };

  const startTask = (task: Task | PlannedTask) => {
    router.push({
      pathname: "/focus",
      params: {
        title: task.title,
        course: String(task.course || ""),
        taskId: task.title,
        dueDate: String(task.due_date || ""),
        minutes: String(taskEstimate(task)),
      },
    });
  };

  const startBlock = (block: Block) => {
    router.push({
      pathname: "/focus",
      params: {
        title: blockTitle(block),
        course: String(block.course || ""),
        blockId: String(block.block_id || ""),
        minutes: String(Math.min(60, Math.max(15, Math.round(blockMinutes(block) || 25)))),
      },
    });
  };

  return (
    <Screen>
      <ReminderOptInPrompt userId={user?.id} />
      <Header
        title={firstName ? `${greeting}, ${firstName}` : greeting}
        subtitle={dateLabel}
        right={
          today.missing ? undefined : (
            <IconButton icon="refresh" label="Rebuild today's briefing" onPress={rebuild} />
          )
        }
      />

      <ScrollView
        contentContainerStyle={{
          padding: space.lg,
          paddingBottom: insets.bottom + 100,
          gap: space.md,
        }}
        refreshControl={
          <RefreshControl
            refreshing={today.refreshing || fallback.refreshing || schedule.refreshing}
            onRefresh={() => {
              if (today.missing) fallback.refresh();
              else today.refresh();
              schedule.refresh();
            }}
            tintColor={colors.accent}
          />
        }
      >
        {live.data && !live.data.ended_at ? (
          <Pressable
            onPress={() => router.push("/focus")}
            accessibilityRole="button"
            accessibilityLabel="Resume your focus session"
          >
            <Card
              style={{
                backgroundColor: colors.accent,
                borderColor: colors.accent,
                flexDirection: "row",
                alignItems: "center",
                gap: space.md,
              }}
            >
              <Ionicons name="timer-outline" size={22} color={colors.onAccent} />
              <View style={{ flex: 1 }}>
                <T variant="sm" tone="onAccent" weight="700">Session in progress</T>
                <T variant="xs" tone="onAccent" numberOfLines={1} style={{ opacity: 0.85 }}>
                  {live.data.title}
                </T>
              </View>
              <Ionicons name="chevron-forward" size={18} color={colors.onAccent} />
            </Card>
          </Pressable>
        ) : null}

        {today.stale || fallback.stale || schedule.stale ? (
          <Notice text="Offline — showing the last version that loaded." icon="cloud-offline-outline" />
        ) : null}
        {today.error && !today.missing && !today.stale ? (
          <Notice text={today.error} icon="alert-circle-outline" />
        ) : null}

        {featuredTask ? (
          <Card
            style={{
              padding: space.lg,
              gap: space.md,
              backgroundColor: colors.accentSoft,
              borderColor: colors.borderAccent,
            }}
          >
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
              <Label style={{ color: colors.accent }}>Do this first</Label>
              <Pressable
                onPress={() => complete(featuredTask.title)}
                disabled={busyTitle === featuredTask.title}
                accessibilityRole="checkbox"
                accessibilityState={{ checked: false, busy: busyTitle === featuredTask.title }}
                accessibilityLabel={`Mark ${featuredTask.title} done`}
                hitSlop={10}
                style={{ padding: 4 }}
              >
                {busyTitle === featuredTask.title ? (
                  <ActivityIndicator size="small" color={colors.accent} />
                ) : (
                  <Ionicons name="ellipse-outline" size={24} color={colors.textMuted} />
                )}
              </Pressable>
            </View>

            <Pressable
              onPress={() => openTask(featuredTask)}
              accessibilityRole="button"
              accessibilityLabel={`View ${featuredTask.title}`}
              style={{ flexDirection: "row", alignItems: "center", gap: space.md }}
            >
              <View
                style={{
                  width: 52,
                  height: 52,
                  borderRadius: radius.lg,
                  backgroundColor: colors.accentMid,
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <Ionicons name="book-outline" size={25} color={colors.accent} />
              </View>
              <View style={{ flex: 1, gap: 4 }}>
                <T variant="md" weight="700" numberOfLines={2}>{featuredTask.title}</T>
                {featuredWhy ? (
                  <T variant="sm" tone="secondary" numberOfLines={2}>{featuredWhy}</T>
                ) : featuredTask.course ? (
                  <T variant="sm" tone="secondary" numberOfLines={1}>{featuredTask.course}</T>
                ) : null}
                <View style={{ flexDirection: "row", alignItems: "center", gap: space.xs, flexWrap: "wrap" }}>
                  <Ionicons name="time-outline" size={14} color={colors.textMuted} />
                  <T variant="xs" tone="muted">{dueLabel(featuredTask.due_date)}</T>
                  {(featuredTask as PlannedTask).est_minutes || (featuredTask as Task).estimated_time ? (
                    <>
                      <T variant="xs" tone="muted">·</T>
                      <T variant="xs" tone="muted">
                        {minutes((featuredTask as PlannedTask).est_minutes ?? (featuredTask as Task).estimated_time)}
                      </T>
                    </>
                  ) : null}
                </View>
              </View>
              <Ionicons name="chevron-forward" size={18} color={colors.textMuted} />
            </Pressable>

            <Button
              title="Start focus"
              icon="play"
              onPress={() => startTask(featuredTask)}
            />
          </Card>
        ) : focusedBlock ? (
          <Card style={{ gap: space.md, backgroundColor: colors.accentSoft, borderColor: colors.borderAccent }}>
            <Label style={{ color: colors.accent }}>Do this first</Label>
            <T variant="md" weight="700">{blockTitle(focusedBlock)}</T>
            <Button title="Start focus" icon="play" onPress={() => startBlock(focusedBlock)} />
          </Card>
        ) : (
          <EmptyState
            icon="sunny-outline"
            title="A little room to breathe"
            body="Your next task or study block will show up here."
            action={<Button title="Add a task" kind="secondary" icon="add" onPress={() => router.push("/new-task")} />}
          />
        )}

        <View style={{ gap: space.sm }}>
          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
            <T variant="lg" weight="700">Your day</T>
            {blockProgress.total ? (
              <T variant="xs" tone="muted">{blockProgress.done} of {blockProgress.total} blocks done</T>
            ) : null}
          </View>

          {todayBlocks.length ? (
            <View>
              {todayBlocks.map((block, index) => {
                const title = blockTitle(block);
                const checked = !!(block.block_id && scheduleProgress[block.block_id]?.done);
                const isLast = index === todayBlocks.length - 1;
                return (
                  <View key={block.block_id || `${title}-${index}`} style={{ flexDirection: "row", gap: space.md }}>
                    <View style={{ width: 58, alignItems: "flex-end", paddingTop: 13 }}>
                      <T variant="xs" tone={checked ? "muted" : "secondary"} numberOfLines={1}>
                        {timeForBlock(block)}
                      </T>
                    </View>
                    <View style={{ width: 14, alignItems: "center" }}>
                      <View
                        style={{
                          width: 9,
                          height: 9,
                          marginTop: 18,
                          borderRadius: radius.pill,
                          backgroundColor: checked
                            ? colors.ok
                            : block.is_break || index !== focusedBlockIndex
                              ? colors.textMuted
                              : colors.accent,
                        }}
                      />
                      {!isLast ? <View style={{ flex: 1, width: 2, minHeight: 46, backgroundColor: colors.border }} /> : null}
                    </View>
                    <Pressable
                      disabled={!!block.is_break}
                      onPress={() => startBlock(block)}
                      accessibilityRole={block.is_break ? undefined : "button"}
                      accessibilityLabel={block.is_break ? title : `Start focus for ${title}`}
                      style={({ pressed }) => ({
                        flex: 1,
                        paddingVertical: space.md,
                        paddingHorizontal: space.md,
                        marginBottom: space.sm,
                        borderRadius: radius.lg,
                        backgroundColor: checked ? colors.bgSecondary : colors.bgCard,
                        borderWidth: 1,
                        borderColor: colors.border,
                        opacity: pressed && !block.is_break ? 0.75 : 1,
                        flexDirection: "row",
                        alignItems: "center",
                        gap: space.sm,
                      })}
                    >
                      <View style={{ flex: 1, gap: 3 }}>
                        <T variant="base" weight="600" numberOfLines={1} style={checked ? { textDecorationLine: "line-through" } : undefined}>
                          {title}
                        </T>
                        <T variant="xs" tone="muted" numberOfLines={1}>
                          {[block.course, blockMinutes(block) ? minutes(blockMinutes(block)) : null, block.is_break ? "Break" : null]
                            .filter(Boolean)
                            .join(" · ") || "Study block"}
                        </T>
                      </View>
                      {!block.is_break ? (
                        <Ionicons name={checked ? "checkmark-circle" : "play-circle-outline"} size={21} color={checked ? colors.ok : colors.accent} />
                      ) : null}
                    </Pressable>
                  </View>
                );
              })}
            </View>
          ) : (
            <Pressable
              onPress={() => router.push("/(tabs)/plan")}
              accessibilityRole="button"
              accessibilityLabel="Build a study plan"
              style={({ pressed }) => ({
                padding: space.lg,
                borderRadius: radius.lg,
                borderWidth: 1,
                borderColor: colors.border,
                backgroundColor: colors.bgCard,
                flexDirection: "row",
                alignItems: "center",
                gap: space.md,
                opacity: pressed ? 0.75 : 1,
              })}
            >
              <Ionicons name="calendar-outline" size={20} color={colors.accent} />
              <View style={{ flex: 1 }}>
                <T variant="base" weight="600">No study blocks today</T>
                <T variant="xs" tone="muted">Build a plan around your schedule.</T>
              </View>
              <Ionicons name="chevron-forward" size={18} color={colors.textMuted} />
            </Pressable>
          )}
        </View>

        {laterTasks.length ? (
          <View style={{ gap: space.sm }}>
            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
              <T variant="lg" weight="700">Up next</T>
              <Pressable onPress={() => router.push("/(tabs)/tasks")} accessibilityRole="link">
                <T variant="sm" tone="accent" weight="600">See all</T>
              </Pressable>
            </View>
            {laterTasks.map((task, index) => (
              <TaskRow
                key={(task as PlannedTask).id || `${task.title}-${index}`}
                task={
                  today.missing
                    ? (task as Task)
                    : {
                        title: task.title,
                        course: task.course,
                        due_date: task.due_date,
                        est_minutes: (task as PlannedTask).est_minutes,
                        priority: (task as PlannedTask).priority?.tier,
                        why_now: (task as PlannedTask).why_now,
                        source: (task as PlannedTask).source,
                      }
                }
                busy={busyTitle === task.title}
                onToggle={() => complete(task.title)}
                onPress={() => openTask(task)}
              />
            ))}
            {tasks.length > 8 ? (
              <Button title="See everything due" kind="secondary" icon="arrow-forward" onPress={() => router.push("/(tabs)/tasks")} />
            ) : null}
          </View>
        ) : null}

        {streak.data?.streak_count ? (
          <View style={{ flexDirection: "row", gap: space.sm, flexWrap: "wrap" }}>
            <Chip label={`${streak.data.streak_count} day streak`} icon="flame" fg={colors.warnText} bg={colors.warnSoft} />
            {streak.data.spark_balance ? <Chip label={`${streak.data.spark_balance} sparks`} icon="sparkles" /> : null}
            {streak.data.level_title ? <Chip label={String(streak.data.level_title)} icon="trophy-outline" /> : null}
            {streak.data.at_risk ? <Chip label="At risk today" icon="alert-circle" fg={colors.dangerText} bg={colors.dangerSoft} /> : null}
          </View>
        ) : null}

        {data?.briefing?.headline ? (
          <Card style={{ backgroundColor: colors.accentSoft, borderColor: colors.borderAccent }}>
            <Label style={{ marginBottom: 6 }}>Your briefing</Label>
            <T variant="md" weight="700">{data.briefing.headline}</T>
            {data.briefing.body ? <T tone="secondary" style={{ marginTop: 6 }}>{data.briefing.body}</T> : null}
            {rebuilding ? <T variant="xs" tone="muted" style={{ marginTop: space.sm }}>Rewriting…</T> : null}
          </Card>
        ) : null}

        {data?.health ? (
          <Card style={{ flexDirection: "row", gap: space.lg, alignItems: "center" }}>
            <Ring score={data.health.score} tier={data.health.tier} />
            <View style={{ flex: 1, gap: 6 }}>
              <Label>Academic health</Label>
              <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm, flexWrap: "wrap" }}>
                <T variant="base" weight="600" style={{ textTransform: "capitalize" }}>
                  {String(data.health.tier || "").replace("_", " ")}
                </T>
                {data.health.delta_vs_yesterday ? (
                  <Chip
                    label={`${data.health.delta_vs_yesterday > 0 ? "+" : ""}${data.health.delta_vs_yesterday} since yesterday`}
                    icon={data.health.delta_vs_yesterday > 0 ? "trending-up" : "trending-down"}
                    fg={data.health.delta_vs_yesterday > 0 ? colors.ok : colors.dangerText}
                    bg={data.health.delta_vs_yesterday > 0 ? colors.okSoft : colors.dangerSoft}
                  />
                ) : null}
              </View>
              {data.health.summary ? <T variant="sm" tone="secondary">{data.health.summary}</T> : null}
            </View>
          </Card>
        ) : null}

        {data?.forecast?.days?.length ? (
          <Card>
            <Label style={{ marginBottom: space.md }}>Next 7 days</Label>
            <ForecastBars days={data.forecast.days} />
            {data.forecast.summary ? <T variant="sm" tone="secondary" style={{ marginTop: space.md }}>{data.forecast.summary}</T> : null}
          </Card>
        ) : null}

        {today.missing ? (
          <Notice tone="accent" text="The AI briefing isn't switched on for your account yet — this is the plain task list in the meantime." />
        ) : null}
      </ScrollView>
    </Screen>
  );
}
