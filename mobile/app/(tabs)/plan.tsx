import React, { useCallback, useMemo, useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { Block, generateSchedule, getSchedule, SavedSchedule, ScheduleDay } from "../../lib/api";
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
  const inner: unknown = (raw as any).days ?? (raw as any).schedule ?? raw;

  if (Array.isArray(inner)) {
    return inner
      .map((d: any) => {
        if (Array.isArray(d)) return { day: undefined, blocks: d as Block[] };
        if (d && typeof d === "object") {
          return {
            day: d.day ?? d.name ?? d.label,
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
  const [genError, setGenError] = useState<string | null>(null);

  const days = useMemo(() => normalise(q.data), [q.data]);

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

  const totalBlocks = days.reduce((n, d) => n + (d.blocks?.length || 0), 0);

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
              {(d.blocks || []).map((b, j) => (
                <Pressable
                  key={j}
                  accessibilityRole="button"
                  accessibilityLabel={`Focus on ${b.title || b.course || "this block"}`}
                  onPress={() =>
                    router.push({
                      pathname: "/focus",
                      params: {
                        title: String(b.title || b.course || "Study block"),
                        course: String(b.course || ""),
                        blockId: String((b as any).id || ""),
                        minutes: String(
                          Math.min(60, Math.max(15, Math.round(Number(b.duration) || 25))),
                        ),
                      },
                    })
                  }
                >
                <Card style={{ padding: space.md, flexDirection: "row", gap: space.md }}>
                  <View
                    style={{
                      width: 4,
                      borderRadius: radius.pill,
                      backgroundColor: colors.accent,
                    }}
                  />
                  <View style={{ flex: 1, gap: 5 }}>
                    <T variant="base" weight="600" numberOfLines={2}>
                      {b.title || b.course || "Study block"}
                    </T>
                    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
                      {blockTime(b) ? <Chip label={blockTime(b)} icon="time-outline" /> : null}
                      {b.course && b.title ? <Chip label={String(b.course)} /> : null}
                      {b.duration ? <Chip label={minutes(b.duration as number)} icon="hourglass-outline" /> : null}
                      {b.kind ? <Chip label={String(b.kind)} /> : null}
                    </View>
                  </View>
                  <Ionicons name="play-circle-outline" size={22} color={colors.accent} />
                </Card>
                </Pressable>
              ))}
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
