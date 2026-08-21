import React, { useMemo, useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { getGrades, getPredictions, GradeRow, Prediction } from "../../lib/api";
import { useQuery } from "../../lib/useQuery";
import { useTheme } from "../../theme/ThemeProvider";
import { radius, space } from "../../theme/tokens";
import { letterFor, toPercent } from "../../lib/format";
import {
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

function courseName(r: GradeRow): string {
  return String(r.course || r.class_name || r.name || "Untitled course");
}

/**
 * Band colours are deliberately muted, and a low grade is amber rather
 * than red: "you are failing" is a judgement a planner has not earned the
 * right to make in a colour the student cannot dismiss.
 */
function band(colors: ReturnType<typeof useTheme>["colors"], p: number | null) {
  if (p === null) return colors.textMuted;
  if (p >= 90) return colors.ok;
  if (p >= 80) return colors.accent;
  if (p >= 70) return colors.warn;
  return colors.danger;
}

export default function GradesScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<"current" | "forecast">("current");

  const grades = useQuery<GradeRow[]>("grades", getGrades);
  // Predictions are an AI call, so they are only made once the student
  // actually opens the forecast tab rather than on every visit to Grades.
  const preds = useQuery<{ predictions: Prediction[]; message?: string }>(
    "predictions",
    getPredictions,
    { enabled: tab === "forecast" },
  );

  const rows = useMemo(() => grades.data || [], [grades.data]);

  const average = useMemo(() => {
    const nums = rows.map((r) => toPercent(r.percentage ?? r.percent ?? r.grade)).filter((n): n is number => n !== null);
    if (!nums.length) return null;
    return nums.reduce((a, b) => a + b, 0) / nums.length;
  }, [rows]);

  if (grades.loading) {
    return (
      <Screen>
        <Header title="Grades" />
        <Loading label="Fetching your grades…" />
      </Screen>
    );
  }

  if (grades.error && !rows.length) {
    return (
      <Screen>
        <Header title="Grades" />
        <ErrorState message={grades.error} onRetry={grades.reload} />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header
        title="Grades"
        subtitle={average !== null ? `${average.toFixed(1)}% average across ${rows.length} courses` : null}
      />

      <View style={{ paddingLeft: space.lg, paddingVertical: space.md }}>
        <SegmentedRow
          options={[
            { label: "Current", value: "current" as const },
            { label: "Forecast", value: "forecast" as const },
          ]}
          value={tab}
          onChange={setTab}
        />
      </View>

      <ScrollView
        contentContainerStyle={{
          paddingHorizontal: space.lg,
          paddingBottom: insets.bottom + 100,
          gap: space.sm,
        }}
        refreshControl={
          <RefreshControl
            refreshing={tab === "current" ? grades.refreshing : preds.refreshing}
            onRefresh={tab === "current" ? grades.refresh : preds.refresh}
            tintColor={colors.accent}
          />
        }
      >
        {grades.stale && tab === "current" ? (
          <Notice text="Offline — showing the last grades that loaded." icon="cloud-offline-outline" />
        ) : null}

        {tab === "current" ? (
          rows.length ? (
            rows.map((r, i) => {
              const p = toPercent(r.percentage ?? r.percent ?? r.grade);
              const colour = band(colors, p);
              return (
                <Card key={`${courseName(r)}-${i}`} style={{ padding: space.md, gap: space.sm }}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
                    <View style={{ flex: 1, gap: 4 }}>
                      <T variant="base" weight="600" numberOfLines={2}>
                        {courseName(r)}
                      </T>
                      <View style={{ flexDirection: "row", gap: 6, flexWrap: "wrap" }}>
                        {r.teacher ? <Chip label={String(r.teacher)} icon="person-outline" /> : null}
                        {r.period ? <Chip label={`Period ${r.period}`} /> : null}
                        {r.source ? <Chip label={String(r.source)} /> : null}
                      </View>
                    </View>
                    <View style={{ alignItems: "flex-end" }}>
                      <T variant="lg" weight="700" style={{ color: colour }}>
                        {p === null ? "—" : `${p.toFixed(1)}%`}
                      </T>
                      <T variant="xs" tone="muted" weight="600">
                        {String(r.letter || letterFor(p))}
                      </T>
                    </View>
                  </View>

                  {p !== null ? (
                    <View
                      style={{
                        height: 6,
                        backgroundColor: colors.bgElevated,
                        borderRadius: radius.pill,
                        overflow: "hidden",
                      }}
                    >
                      <View
                        style={{
                          width: `${Math.max(0, Math.min(100, p))}%`,
                          height: "100%",
                          backgroundColor: colour,
                          borderRadius: radius.pill,
                        }}
                      />
                    </View>
                  ) : null}
                </Card>
              );
            })
          ) : (
            <EmptyState
              icon="stats-chart-outline"
              title="No grades yet"
              body="Connect Canvas, StudentVue, Schoology or Blackboard on the website and your grades appear here."
            />
          )
        ) : preds.loading ? (
          <Loading label="Working out where each course is heading…" />
        ) : preds.missing ? (
          <EmptyState
            icon="telescope-outline"
            title="Forecasts aren't enabled"
            body="Your account doesn't have grade prediction switched on yet."
          />
        ) : preds.error ? (
          <ErrorState message={preds.error} onRetry={preds.reload} />
        ) : preds.data?.predictions?.length ? (
          preds.data.predictions.map((p) => <PredictionCard key={p.course} p={p} />)
        ) : (
          <EmptyState
            icon="telescope-outline"
            title="Not enough to go on"
            body={preds.data?.message || "There aren't enough graded assignments yet to forecast anything honestly."}
          />
        )}
      </ScrollView>
    </Screen>
  );
}

function PredictionCard({ p }: { p: Prediction }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);

  const move = p.current_percent === null ? null : p.predicted_percent - p.current_percent;
  const trend = String(p.trend || "").toLowerCase();
  const trendColour =
    trend === "improving" ? colors.ok : trend === "slipping" ? colors.danger : colors.textMuted;
  const trendIcon =
    trend === "improving" ? "trending-up" : trend === "slipping" ? "trending-down" : "remove";

  return (
    <Pressable onPress={() => setOpen((o) => !o)} accessibilityRole="button">
      <Card style={{ padding: space.md, gap: space.sm }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
          <View style={{ flex: 1, gap: 4 }}>
            <T variant="base" weight="600" numberOfLines={2}>
              {p.course}
            </T>
            <View style={{ flexDirection: "row", gap: 6, flexWrap: "wrap" }}>
              <Chip
                label={trend || "steady"}
                icon={trendIcon as any}
                fg={trendColour}
                bg={colors.bgElevated}
              />
              {/* Confidence is shown next to every number on purpose: a
                  forecast from four assignments should not look as solid
                  as one from forty. */}
              <Chip label={`${Math.round((p.confidence || 0) * 100)}% confidence`} />
              <Chip label={`${p.sample_size} graded`} />
            </View>
          </View>
          <View style={{ alignItems: "flex-end" }}>
            <T variant="lg" weight="700" style={{ color: band(colors, p.predicted_percent) }}>
              {p.predicted_percent.toFixed(1)}%
            </T>
            {move !== null ? (
              <T variant="xs" tone={move >= 0 ? "ok" : "danger"} weight="600">
                {move >= 0 ? "+" : ""}
                {move.toFixed(1)} pts
              </T>
            ) : null}
          </View>
        </View>

        {open && p.narrative ? (
          <T variant="sm" tone="secondary">
            {p.narrative}
          </T>
        ) : p.narrative ? (
          <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
            <T variant="xs" tone="accent" weight="600">
              Why
            </T>
            <Ionicons name="chevron-down" size={12} color={colors.accent} />
          </View>
        ) : null}
      </Card>
    </Pressable>
  );
}

