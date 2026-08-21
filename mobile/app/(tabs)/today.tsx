import React, { useCallback, useState } from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import {
  dismissTask,
  getStreak,
  getTasks,
  getToday,
  refreshToday,
  Task,
  TodayPayload,
} from "../../lib/api";
import { useQuery } from "../../lib/useQuery";
import { useAuth } from "../../lib/auth";
import { useTheme } from "../../theme/ThemeProvider";
import { space } from "../../theme/tokens";
import { Button, Card, Chip, EmptyState, Label, Loading, Notice, Screen, T } from "../../components/ui";
import { Header, IconButton } from "../../components/Header";
import { Ring } from "../../components/Ring";
import { ForecastBars } from "../../components/Bars";
import { TaskRow } from "../../components/TaskRow";

export default function TodayScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();

  const today = useQuery<TodayPayload>("today", getToday);
  const streak = useQuery<Awaited<ReturnType<typeof getStreak>>>("streak", getStreak);

  /**
   * The Command Center is behind a feature flag, and a school that has not
   * enabled it gets a 404 rather than an empty payload. Falling back to the
   * unified task list means the first screen is never blank — it just shows
   * the plain version of the same question.
   */
  const fallback = useQuery<Task[]>("today-fallback", getTasks, { enabled: today.missing });

  const [busyTitle, setBusyTitle] = useState<string | null>(null);
  const [done, setDone] = useState<Set<string>>(new Set());
  const [rebuilding, setRebuilding] = useState(false);

  const complete = useCallback(
    async (title: string) => {
      setBusyTitle(title);
      try {
        await dismissTask(title);
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
        // Kept locally rather than refetching: /api/today is cached
        // server-side for a minute, so a reload right now would hand back
        // the task we just ticked off and it would visibly reappear.
        setDone((prev) => new Set(prev).add(title));
      } catch {
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
      } finally {
        setBusyTitle(null);
      }
    },
    [],
  );

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

  if (loading) {
    return (
      <Screen>
        <Header title="Today" />
        <Loading label="Building your day…" />
      </Screen>
    );
  }

  const plan = (data?.plan || []).filter((t) => !done.has(t.title));
  const fallbackTasks = (fallback.data || []).filter((t) => !done.has(t.title));

  return (
    <Screen>
      <Header
        title={firstName ? `${greeting}, ${firstName}` : greeting}
        subtitle={data?.forecast?.summary || null}
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
            refreshing={today.refreshing || fallback.refreshing}
            onRefresh={() => (today.missing ? fallback.refresh() : today.refresh())}
            tintColor={colors.accent}
          />
        }
      >
        {today.stale || fallback.stale ? (
          <Notice text="Offline — showing the last version that loaded." icon="cloud-offline-outline" />
        ) : null}
        {today.error && !today.missing && !today.stale ? (
          <Notice text={today.error} icon="alert-circle-outline" />
        ) : null}

        {/* Streak strip. Small on purpose: it is motivation, not the point
            of the screen, and a big gamified banner above real deadlines
            gets the priority backwards. */}
        {streak.data?.streak_count ? (
          <View style={{ flexDirection: "row", gap: space.sm, flexWrap: "wrap" }}>
            <Chip
              label={`${streak.data.streak_count} day streak`}
              icon="flame"
              fg={colors.warnText}
              bg={colors.warnSoft}
            />
            {streak.data.spark_balance ? (
              <Chip label={`${streak.data.spark_balance} sparks`} icon="sparkles" />
            ) : null}
            {streak.data.level_title ? <Chip label={String(streak.data.level_title)} icon="trophy-outline" /> : null}
            {streak.data.at_risk ? (
              <Chip
                label="At risk today"
                icon="alert-circle"
                fg={colors.dangerText}
                bg={colors.dangerSoft}
              />
            ) : null}
          </View>
        ) : null}

        {/* ── Briefing ── */}
        {data?.briefing?.headline ? (
          <Card style={{ backgroundColor: colors.accentSoft, borderColor: colors.borderAccent }}>
            <Label style={{ marginBottom: 6 }}>Your briefing</Label>
            <T variant="md" weight="700">
              {data.briefing.headline}
            </T>
            {data.briefing.body ? (
              <T tone="secondary" style={{ marginTop: 6 }}>
                {data.briefing.body}
              </T>
            ) : null}
            {rebuilding ? (
              <T variant="xs" tone="muted" style={{ marginTop: space.sm }}>
                Rewriting…
              </T>
            ) : null}
          </Card>
        ) : null}

        {/* ── Health + forecast ── */}
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
              {data.health.summary ? (
                <T variant="sm" tone="secondary">
                  {data.health.summary}
                </T>
              ) : null}
            </View>
          </Card>
        ) : null}

        {data?.forecast?.days?.length ? (
          <Card>
            <Label style={{ marginBottom: space.md }}>Next 7 days</Label>
            <ForecastBars days={data.forecast.days} />
            {data.forecast.summary ? (
              <T variant="sm" tone="secondary" style={{ marginTop: space.md }}>
                {data.forecast.summary}
              </T>
            ) : null}
          </Card>
        ) : null}

        {/* ── The plan ── */}
        <View style={{ gap: space.sm, marginTop: space.sm }}>
          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
            <Label>{today.missing ? "Due soon" : "Do this first"}</Label>
            <T variant="xs" tone="muted">
              {(today.missing ? fallbackTasks : plan).length} item
              {(today.missing ? fallbackTasks : plan).length === 1 ? "" : "s"}
            </T>
          </View>

          {today.missing ? (
            fallbackTasks.length ? (
              fallbackTasks
                .slice(0, 8)
                .map((t, i) => (
                  <TaskRow
                    key={`${t.title}-${i}`}
                    task={t}
                    busy={busyTitle === t.title}
                    onToggle={() => complete(t.title)}
                  />
                ))
            ) : (
              <EmptyState
                icon="checkmark-done-outline"
                title="Nothing due"
                body="Connect Canvas, StudentVue or Schoology from the website and your work shows up here automatically."
              />
            )
          ) : plan.length ? (
            plan.slice(0, 8).map((t) => (
              <TaskRow
                key={t.id}
                task={{
                  title: t.title,
                  course: t.course,
                  due_date: t.due_date,
                  est_minutes: t.est_minutes,
                  priority: t.priority?.tier,
                  why_now: t.why_now,
                  source: t.source,
                }}
                busy={busyTitle === t.title}
                onToggle={() => complete(t.title)}
              />
            ))
          ) : (
            <EmptyState
              icon="checkmark-done-outline"
              title="You're clear"
              body="Nothing is competing for your attention right now."
              action={
                <Button
                  title="Add a task"
                  kind="secondary"
                  icon="add"
                  onPress={() => router.push("/new-task")}
                />
              }
            />
          )}
        </View>

        {(today.missing ? fallbackTasks : plan).length > 8 ? (
          <Button
            title="See everything due"
            kind="secondary"
            icon="arrow-forward"
            onPress={() => router.push("/(tabs)/tasks")}
          />
        ) : null}

        {today.missing ? (
          <Notice
            tone="accent"
            text="The AI briefing isn't switched on for your account yet — this is the plain list in the meantime."
          />
        ) : null}
      </ScrollView>
    </Screen>
  );
}
