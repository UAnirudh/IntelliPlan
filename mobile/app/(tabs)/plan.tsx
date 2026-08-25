import React, { useCallback, useMemo, useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import {
  Block,
  blockMinutes,
  blockTitle,
  generateSchedule,
  getSchedule,
  getTasks,
  Recovery,
  recoverSchedule,
  SavedSchedule,
  ScheduleDay,
  ScheduleProgress,
  saveScheduleProgress,
} from "../../lib/api";
import { useQuery } from "../../lib/useQuery";
import { useTheme } from "../../theme/ThemeProvider";
import { radius, space } from "../../theme/tokens";
import { minutes, shortDate } from "../../lib/format";
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
  SegmentedRow,
  T,
} from "../../components/ui";
import { Header } from "../../components/Header";

const HOURS = [
  { label: "1 hour", value: 1 },
  { label: "2 hours", value: 2 },
  { label: "3 hours", value: 3 },
  { label: "4 hours", value: 4 },
];

const TIMES = [
  { label: "Morning", value: "morning" },
  { label: "Afternoon", value: "afternoon" },
  { label: "Evening", value: "evening" },
];

/**
 * Every generator in the codebase has answered in a slightly different
 * shape over time — a list of days, a dict keyed by weekday, or the days
 * nested under `schedule`. Normalising here means the renderer below is
 * written once instead of branching on which one arrived.
 */
function normalise(raw: SavedSchedule | null): ScheduleDay[] {
  if (!raw) return [];
  // `data.schedule` first: that is where both live endpoints actually put
  // the days. The others are older shapes kept so a plan saved before this
  // still renders.
  const inner: unknown =
    (raw as any).data?.schedule ??
    (raw as any).days ??
    (raw as any).schedule ??
    (raw as any).data ??
    raw;

  if (Array.isArray(inner)) {
    return inner
      .map((d: any) => {
        if (Array.isArray(d)) return { day: undefined, blocks: d as Block[] };
        if (d && typeof d === "object") {
          return {
            day: d.day ?? d.day_name ?? d.name ?? d.label,
            date: d.date,
            blocks: (d.blocks ?? d.tasks ?? d.items ?? []) as Block[],
          };
        }
        return { blocks: [] };
      })
      .filter((d) => (d.blocks || []).length);
  }

  if (inner && typeof inner === "object") {
    return Object.entries(inner as Record<string, unknown>)
      .filter(([, v]) => Array.isArray(v))
      .map(([day, v]) => ({ day, blocks: v as Block[] }))
      .filter((d) => (d.blocks || []).length);
  }

  return [];
}

function blockTime(b: Block): string {
  if (b.time_slot) return String(b.time_slot);
  if (b.start && b.end) return `${b.start} – ${b.end}`;
  if (b.start) return String(b.start);
  return "";
}

export default function PlanScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const q = useQuery<SavedSchedule>("schedule", getSchedule);
  const [hours, setHours] = useState(2);
  const [when, setWhen] = useState("evening");
  const [busy, setBusy] = useState(false);
  const [catching, setCatching] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [recovery, setRecovery] = useState<Recovery | null>(null);

  const days = useMemo(() => normalise(q.data), [q.data]);

  /**
   * Which blocks are ticked off.
   *
   * Seeded from the server on every load and then held here, so a tap
   * updates instantly rather than after a round trip. The server is told
   * separately; a failure there leaves the tick on screen and the note
   * below explains why, which is better than a checkbox that springs back.
   */
  const [progress, setProgress] = useState<ScheduleProgress>({});
  const serverProgress = q.data?.progress;
  React.useEffect(() => {
    setProgress(serverProgress || {});
  }, [serverProgress]);

  const [progressNote, setProgressNote] = useState<string | null>(null);

  const toggleBlock = useCallback(
    async (b: Block) => {
      const key = b.block_id;
      // No block_id means no stable key to record against — an older saved
      // plan that predates them. The server backfills on read, so this is
      // rare; ticking anyway would write against `undefined` and mark every
      // such block at once.
      if (!key) {
        setProgressNote("This block is too old to tick off. Rebuild the plan to fix it.");
        return;
      }
      const next: ScheduleProgress = {
        ...progress,
        [key]: { ...(progress[key] || {}), done: !progress[key]?.done },
      };
      setProgress(next);
      Haptics.selectionAsync().catch(() => {});
      try {
        await saveScheduleProgress(next);
        setProgressNote(null);
      } catch (e: any) {
        setProgressNote("Ticked here, but not saved to your account yet.");
      }
    },
    [progress],
  );

  /** Blocks done and minutes done, against the whole plan. */
  const stats = useMemo(() => {
    let total = 0;
    let done = 0;
    let totalMin = 0;
    let doneMin = 0;
    for (const d of days) {
      for (const b of d.blocks || []) {
        if (b.is_break) continue;
        total += 1;
        const m = blockMinutes(b);
        totalMin += m;
        if (b.block_id && progress[b.block_id]?.done) {
          done += 1;
          doneMin += m;
        }
      }
    }
    return { total, done, totalMin, doneMin, pct: total ? done / total : 0 };
  }, [days, progress]);

  /**
   * Re-solve the plan around what actually happened.
   *
   * The assignments go up with the request because the planner needs the
   * same work the student is looking at. `changed: false` is a real answer
   * — the plan is on track — and is reported as such rather than as a
   * no-op.
   */
  const catchUp = useCallback(async () => {
    setCatching(true);
    setGenError(null);
    setRecovery(null);
    try {
      const assignments = await getTasks();
      const r = await recoverSchedule({
        assignments,
        hoursPerDay: hours,
        preferredTime: when,
      });
      setRecovery(r);
      if (r.changed && r.data) {
        q.setData(() => ({ status: "ok", data: r.data, progress }) as SavedSchedule);
      }
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setGenError(e?.message || "Couldn't catch the plan up.");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setCatching(false);
    }
  }, [hours, when, q, progress]);

  const build = useCallback(async () => {
    setBusy(true);
    setGenError(null);
    try {
      const fresh = await generateSchedule(hours, when);
      q.setData(() => fresh);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setGenError(e?.message || "Couldn't build a plan just now.");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setBusy(false);
    }
  }, [hours, when, q]);

  if (q.loading) {
    return (
      <Screen>
        <Header title="Plan" />
        <Loading label="Loading your plan…" />
      </Screen>
    );
  }

  if (q.error && !q.data) {
    return (
      <Screen>
        <Header title="Plan" />
        <ErrorState message={q.error} onRetry={q.reload} />
      </Screen>
    );
  }

  // Breaks are in the plan but are not work, so they are left out here for
  // the same reason they are left out of the progress count — a header
  // saying "5 study blocks" over "0 of 4 blocks done" is just wrong.
  const totalBlocks = stats.total;

  return (
    <Screen>
      <Header
        title="Plan"
        subtitle={totalBlocks ? `${totalBlocks} study block${totalBlocks === 1 ? "" : "s"}` : "No plan yet"}
      />

      <ScrollView
        contentContainerStyle={{
          padding: space.lg,
          paddingBottom: insets.bottom + 100,
          gap: space.md,
        }}
        refreshControl={
          <RefreshControl refreshing={q.refreshing} onRefresh={q.refresh} tintColor={colors.accent} />
        }
      >
        {q.stale ? (
          <Notice text="Offline — showing the last plan that loaded." icon="cloud-offline-outline" />
        ) : null}

        {stats.total ? (
          <Card style={{ gap: space.md }}>
            <View style={{ flexDirection: "row", alignItems: "flex-end", gap: space.sm }}>
              <View style={{ flex: 1 }}>
                <Label>Progress</Label>
                <T variant="sm" tone="secondary" style={{ marginTop: 4 }}>
                  {stats.done} of {stats.total} block{stats.total === 1 ? "" : "s"} done
                  {/* `minutes(0)` is empty by design — it exists to make a
                      zero chip disappear — so nothing done needs saying
                      explicitly here, or the line reads "· of 5h 15m". */}
                  {stats.totalMin
                    ? ` · ${minutes(stats.doneMin) || "0m"} of ${minutes(stats.totalMin)}`
                    : ""}
                </T>
              </View>
              <T variant="lg" weight="700">{Math.round(stats.pct * 100)}%</T>
            </View>

            <View
              accessibilityRole="progressbar"
              accessibilityValue={{ min: 0, max: stats.total, now: stats.done }}
              style={{
                height: 8,
                borderRadius: radius.pill,
                backgroundColor: colors.bgElevated,
                overflow: "hidden",
              }}
            >
              <View
                style={{
                  width: `${Math.round(stats.pct * 100)}%`,
                  height: "100%",
                  backgroundColor: stats.pct >= 1 ? colors.ok : colors.accent,
                }}
              />
            </View>

            {progressNote ? <Notice text={progressNote} icon="cloud-offline-outline" /> : null}

            {/* Falling behind is the normal case, not the exception. The
                naive fix — shove today's misses onto tomorrow — builds a
                day nobody can do; this re-solves the whole remaining week
                and credits what was actually done. */}
            <Button
              title="Catch me up"
              kind="secondary"
              icon="refresh"
              busy={catching}
              onPress={catchUp}
            />
            <T variant="xs" tone="muted" style={{ textAlign: "center" }}>
              Re-plans what's left around the sessions you've missed.
            </T>

            {recovery ? (
              recovery.changed ? (
                <View style={{ gap: 6 }}>
                  <Notice
                    tone="accent"
                    icon="sparkles-outline"
                    text={`Plan rebuilt${
                      recovery.missed_minutes
                        ? ` around ${minutes(recovery.missed_minutes)} of missed work`
                        : ""
                    }.`}
                  />
                  {(recovery.changes || []).slice(0, 5).map((c, i) => (
                    <T key={i} variant="xs" tone="muted">
                      • {c.detail || c.title || c.kind}
                    </T>
                  ))}
                  {recovery.overloaded ? (
                    <Notice
                      icon="alert-circle-outline"
                      text="Even re-planned, there's more work than time. Push a due date or add hours."
                    />
                  ) : null}
                </View>
              ) : (
                <Notice
                  icon="checkmark-circle-outline"
                  text={recovery.message || "Your plan is still on track — nothing needed moving."}
                />
              )
            ) : null}
          </Card>
        ) : null}

        {/* Generating is a button, never automatic on open: rebuilding on
            every launch would silently discard the progress ticked off
            against the existing plan, and spend an AI call nobody asked
            for. */}
        <Card style={{ gap: space.md }}>
          <View>
            <Label>{totalBlocks ? "Rebuild your plan" : "Build a study plan"}</Label>
            <T variant="sm" tone="secondary" style={{ marginTop: 4 }}>
              IntelliPlan spreads what's due across the days you have, weighted by
              deadline and how long each piece takes.
            </T>
          </View>

          <View style={{ gap: space.sm }}>
            <Label>Hours per day</Label>
            <SegmentedRow options={HOURS} value={hours} onChange={setHours} />
          </View>

          <View style={{ gap: space.sm }}>
            <Label>When you focus best</Label>
            <SegmentedRow options={TIMES} value={when} onChange={setWhen} />
          </View>

          {genError ? <Notice text={genError} icon="alert-circle-outline" /> : null}

          <Button
            title={totalBlocks ? "Rebuild plan" : "Build my plan"}
            icon="sparkles"
            busy={busy}
            onPress={build}
          />
          {/* The generator answers "fit my work into my free time". Some
              students need the other question — "here is when I actually
              study, work around it" — and no hours-per-day dial expresses
              swimming on Tuesdays. */}
          <Button
            title="Set my own hours instead"
            kind="secondary"
            icon="create-outline"
            onPress={() => router.push("/plan-custom")}
          />
          {totalBlocks ? (
            <T variant="xs" tone="muted" style={{ textAlign: "center" }}>
              Rebuilding replaces the plan below.
            </T>
          ) : null}
        </Card>

        {days.length ? (
          days.map((d, i) => (
            <View key={`${d.day ?? d.date ?? i}`} style={{ gap: space.sm }}>
              <Label style={{ marginTop: space.sm }}>
                {d.day || shortDate(d.date) || `Day ${i + 1}`}
              </Label>
              {(d.blocks || []).map((b, j) => {
                const label = blockTitle(b);
                const mins = blockMinutes(b);
                const ticked = !!(b.block_id && progress[b.block_id]?.done);
                return (
                <Card
                  key={b.block_id || j}
                  style={{
                    padding: space.md,
                    flexDirection: "row",
                    gap: space.md,
                    alignItems: "center",
                    opacity: ticked ? 0.6 : 1,
                    borderColor: b.unplaced ? colors.warn : undefined,
                  }}
                >
                  <View
                    style={{
                      width: 4,
                      alignSelf: "stretch",
                      borderRadius: radius.pill,
                      backgroundColor: b.is_break ? colors.textSecondary : colors.accent,
                    }}
                  />

                  {/* A break is something to take, not to tick off, and an
                      unplaced block has no time to be done at — offering a
                      checkbox on either would record progress that means
                      nothing. */}
                  {b.is_break || b.unplaced ? null : (
                    <Pressable
                      hitSlop={8}
                      onPress={() => toggleBlock(b)}
                      accessibilityRole="checkbox"
                      accessibilityState={{ checked: ticked }}
                      accessibilityLabel={`Mark ${label} ${ticked ? "not done" : "done"}`}
                    >
                      <Ionicons
                        name={ticked ? "checkmark-circle" : "ellipse-outline"}
                        size={24}
                        color={ticked ? colors.ok : colors.textSecondary}
                      />
                    </Pressable>
                  )}

                  <Pressable
                    style={{ flex: 1, gap: 5 }}
                    disabled={!!b.is_break}
                    accessibilityRole="button"
                    accessibilityLabel={`Focus on ${label}`}
                    onPress={() =>
                      router.push({
                        pathname: "/focus",
                        params: {
                          title: label,
                          course: String(b.course || ""),
                          blockId: String(b.block_id || ""),
                          minutes: String(Math.min(60, Math.max(15, Math.round(mins || 25)))),
                        },
                      })
                    }
                  >
                    <T
                      variant="base"
                      weight="600"
                      numberOfLines={2}
                      style={ticked ? { textDecorationLine: "line-through" } : undefined}
                    >
                      {label}
                    </T>
                    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
                      {blockTime(b) ? <Chip label={blockTime(b)} icon="time-outline" /> : null}
                      {b.course && (b.assignment || b.title) ? <Chip label={String(b.course)} /> : null}
                      {mins ? <Chip label={minutes(mins)} icon="hourglass-outline" /> : null}
                      {b.kind ? <Chip label={String(b.kind)} /> : null}
                      {b.unplaced ? (
                        <Chip label="No room" icon="alert-circle-outline" fg={colors.warn} bg={colors.warnSoft} />
                      ) : null}
                    </View>
                  </Pressable>

                  {b.is_break ? null : (
                    <Ionicons name="play-circle-outline" size={22} color={colors.accent} />
                  )}
                </Card>
                );
              })}
            </View>
          ))
        ) : (
          <EmptyState
            icon="calendar-outline"
            title="No plan saved"
            body="Pick your hours above and IntelliPlan will lay out the week."
          />
        )}
      </ScrollView>
    </Screen>
  );
}
