import React, { useMemo } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { getMyStats, MyStats } from "../lib/api";
import { useQuery } from "../lib/useQuery";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, EmptyState, ErrorState, Label, Loading, Notice, Screen, T } from "../components/ui";
import { Header } from "../components/Header";

/** Native progress overview backed by the same account-scoped stats as web. */
export default function StatsScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const stats = useQuery<MyStats>("my-stats", getMyStats);
  const activity = useMemo(() => {
    const days: Array<{ key: string; short: string; tasks: number; study: number }> = [];
    const now = new Date();
    for (let offset = 13; offset >= 0; offset -= 1) {
      const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - offset));
      const key = day.toISOString().slice(0, 10);
      days.push({
        key,
        short: day.toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" }),
        tasks: Number(stats.data?.daily_completions?.[key] || 0),
        study: Number(stats.data?.daily_study_minutes?.[key] || 0),
      });
    }
    return days;
  }, [stats.data]);
  const maxTasks = Math.max(1, ...activity.map((day) => day.tasks));
  const maxStudy = Math.max(1, ...activity.map((day) => day.study));

  if (stats.loading && !stats.data) {
    return <Screen><Header title="My Stats" subtitle="Your progress, over time" /><Loading label="Loading your progress…" /></Screen>;
  }

  return (
    <Screen>
      <Header title="My Stats" subtitle="Small steps add up" showSettings />
      <ScrollView
        contentContainerStyle={{ padding: space.lg, paddingBottom: insets.bottom + 104, gap: space.lg }}
        refreshControl={<RefreshControl refreshing={stats.refreshing} onRefresh={stats.refresh} tintColor={colors.accent} />}
      >
        {stats.stale ? <Notice tone="accent" text="Showing your last saved progress. Pull down to refresh." icon="cloud-offline-outline" /> : null}
        {stats.error && !stats.data ? <ErrorState message={stats.error} onRetry={stats.reload} /> : null}
        {stats.data ? <>
          {stats.error ? <Notice text="Couldn't refresh right now. Your saved progress is still here." icon="cloud-offline-outline" /> : null}
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm }}>
            <Metric label="Tasks done" value={stats.data.tasks_completed} icon="checkmark-done-outline" />
            <Metric label="Study time" value={formatMinutes(stats.data.study_minutes)} icon="time-outline" />
            <Metric label="Day streak" value={stats.data.current_streak} icon="flame-outline" />
            <Metric label="Quiz accuracy" value={stats.data.accuracy == null ? "—" : `${Math.round(stats.data.accuracy)}%`} icon="sparkles-outline" />
          </View>

          <View style={{ gap: space.sm }}>
            <View style={{ gap: 3 }}>
              <T variant="lg" weight="700">Your last two weeks</T>
              <T variant="xs" tone="muted">A quick view of finished tasks and focused study.</T>
            </View>
            <Card style={{ gap: space.lg }}>
              <ActivityBars title="Tasks completed" unit="tasks" days={activity} valueKey="tasks" max={maxTasks} color={colors.accent} />
              <ActivityBars title="Study time" unit="min" days={activity} valueKey="study" max={maxStudy} color={colors.ok} />
            </Card>
          </View>

          <View style={{ gap: space.sm }}>
            <T variant="lg" weight="700">Study performance</T>
            <Card style={{ flexDirection: "row", flexWrap: "wrap", gap: space.md }}>
              <Detail label="Sessions" value={stats.data.study_sessions} />
              <Detail label="Questions answered" value={stats.data.questions_answered} />
              <Detail label="Correct answers" value={stats.data.questions_correct} />
              <Detail label="Concepts mastered" value={`${stats.data.mastery_mastered} / ${stats.data.mastery_concepts}`} />
            </Card>
          </View>

          {stats.data.grades?.length ? <View style={{ gap: space.sm }}>
            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
              <T variant="lg" weight="700">Grades</T>
              <Pressable onPress={() => router.push("/(tabs)/grades")} accessibilityRole="link" accessibilityLabel="Open grade modeler">
                <T variant="sm" tone="accent" weight="600">Grade modeler</T>
              </Pressable>
            </View>
            <Card style={{ paddingVertical: space.xs }}>
              {stats.data.grades.slice(0, 6).map((grade, index) => (
                <View key={`${grade.course}-${index}`} style={{ flexDirection: "row", alignItems: "center", paddingVertical: space.sm, borderBottomWidth: index === Math.min(stats.data!.grades.length, 6) - 1 ? 0 : 1, borderBottomColor: colors.border }}>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <T variant="sm" weight="600" numberOfLines={1}>{grade.course || "Class"}</T>
                    {grade.letter ? <T variant="xs" tone="muted">{grade.letter}</T> : null}
                  </View>
                  <T variant="md" weight="700">{Math.round(Number(grade.percentage) || 0)}%</T>
                </View>
              ))}
              {stats.data.grades.length > 6 ? <T variant="xs" tone="muted" style={{ paddingTop: space.sm }}>Showing 6 of {stats.data.grades.length} classes</T> : null}
            </Card>
          </View> : null}

          {!stats.data.tasks_completed && !stats.data.study_sessions ? <EmptyState
            icon="leaf-outline"
            title="Your progress starts here"
            body="Finish a task or start a study session and your learning history will appear here."
            action={<Button title="Open study tools" icon="albums-outline" onPress={() => router.push("/study")} />}
          /> : null}
        </> : null}
      </ScrollView>
    </Screen>
  );
}

function Metric({ label, value, icon }: { label: string; value: string | number; icon: keyof typeof Ionicons.glyphMap }) {
  const { colors } = useTheme();
  return (
    <Card style={{ flexGrow: 1, flexBasis: "46%", minWidth: 138, gap: space.sm }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: space.xs }}>
        <Ionicons name={icon} size={16} color={colors.accent} />
        <Label>{label}</Label>
      </View>
      <T variant="xl" weight="700" accessibilityLabel={`${label}: ${value}`}>{value}</T>
    </Card>
  );
}

function Detail({ label, value }: { label: string; value: string | number }) {
  return (
    <View style={{ flexGrow: 1, flexBasis: "42%", gap: 3 }}>
      <T variant="lg" weight="700">{value}</T>
      <T variant="xs" tone="muted">{label}</T>
    </View>
  );
}

function ActivityBars({
  title, unit, days, valueKey, max, color,
}: {
  title: string;
  unit: string;
  days: Array<{ key: string; short: string; tasks: number; study: number }>;
  valueKey: "tasks" | "study";
  max: number;
  color: string;
}) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: space.sm }}>
      <T variant="sm" weight="600">{title}</T>
      <View accessibilityRole="image" accessibilityLabel={`${title} for the last fourteen days`} style={{ height: 94, flexDirection: "row", alignItems: "flex-end", gap: 4 }}>
        {days.map((day, index) => {
          const value = day[valueKey];
          const height = value ? Math.max(7, (value / max) * 64) : 3;
          return <View key={day.key} accessibilityLabel={`${day.short}: ${value} ${unit}`} style={{ flex: 1, alignItems: "center", justifyContent: "flex-end", height: "100%", gap: 5 }}>
            <View style={{ width: "100%", height, borderRadius: radius.pill, backgroundColor: value ? color : colors.border }} />
            {index % 3 === 0 || index === days.length - 1 ? <T variant="micro" tone="muted">{day.short.slice(0, 1)}</T> : <View style={{ height: 12 }} />}
          </View>;
        })}
      </View>
    </View>
  );
}

function formatMinutes(value: number) {
  const minutes = Math.max(0, Number(value) || 0);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}
