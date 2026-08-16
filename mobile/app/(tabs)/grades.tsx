import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View,
} from "react-native";
import { getGrades } from "../../lib/api";

/**
 * Course grades from whichever platform the student connected.
 *
 * The server normalises every provider to one list, so this renders the
 * same way whether the grades came from StudentVue, Canvas, Schoology, or
 * a CSV import. Fields beyond course/percentage/letter vary by source and
 * are simply not shown rather than guessed at.
 */

type Row = {
  course?: string;
  class_name?: string;
  percentage?: number | string;
  grade?: string | number;
  letter?: string;
  teacher?: string;
  period?: string;
  source?: string;
};

function pct(row: Row): number | null {
  const raw = row.percentage ?? row.grade;
  if (raw === undefined || raw === null || raw === "") return null;
  const n = typeof raw === "number" ? raw : parseFloat(String(raw).replace("%", ""));
  return Number.isFinite(n) ? n : null;
}

/** Colour by band. Deliberately muted — a red pill on a 68 is a judgement
 *  a planner has not earned the right to make. */
function bandColour(p: number | null): string {
  if (p === null) return "#8c8c87";
  if (p >= 90) return "#2e7d32";
  if (p >= 80) return "#558b2f";
  if (p >= 70) return "#f9a825";
  return "#c62828";
}

export default function GradesScreen() {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await getGrades();
      setRows(Array.isArray(d?.grades) ? d.grades : []);
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Could not load grades.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const graded = rows.map(pct).filter((p): p is number => p !== null);
  const average = graded.length
    ? Math.round((graded.reduce((a, b) => a + b, 0) / graded.length) * 10) / 10
    : null;

  if (loading) return <View style={styles.center}><ActivityIndicator /></View>;

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.error}>{error}</Text>
        <Pressable style={styles.retry} onPress={() => { setLoading(true); load(); }}>
          <Text style={styles.retryText}>Try again</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.wrap}>
      {average !== null ? (
        <View style={styles.summary}>
          <Text style={styles.summaryLabel}>Average across {graded.length} course{graded.length === 1 ? "" : "s"}</Text>
          <Text style={[styles.summaryValue, { color: bandColour(average) }]}>{average}%</Text>
        </View>
      ) : null}

      <FlatList
        data={rows}
        keyExtractor={(item, i) => `${item.course || item.class_name || "course"}-${i}`}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />
        }
        ListEmptyComponent={
          <View style={styles.center}>
            <Text style={styles.empty}>No grades yet.</Text>
            <Text style={styles.emptySub}>
              Connect a school platform on the website, and grades will appear here.
            </Text>
          </View>
        }
        renderItem={({ item }) => {
          const p = pct(item);
          const name = item.course || item.class_name || "Untitled course";
          const letter = item.letter || (typeof item.grade === "string" ? item.grade : "");
          return (
            <View style={styles.row}>
              <View style={styles.rowBody}>
                <Text style={styles.rowTitle} numberOfLines={2}>{name}</Text>
                {(item.teacher || item.period) ? (
                  <Text style={styles.rowMeta}>
                    {[item.teacher, item.period ? `Period ${item.period}` : null]
                      .filter(Boolean).join(" · ")}
                  </Text>
                ) : null}
              </View>
              <View style={styles.scoreBox}>
                <Text style={[styles.score, { color: bandColour(p) }]}>
                  {p === null ? "—" : `${p}%`}
                </Text>
                {letter ? <Text style={styles.letter}>{letter}</Text> : null}
              </View>
            </View>
          );
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, padding: 16, backgroundColor: "#f5f4f1" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  error: { color: "#c62828", textAlign: "center" },
  retry: { marginTop: 12, paddingVertical: 10, paddingHorizontal: 20, borderRadius: 999, backgroundColor: "#1a1a1a" },
  retryText: { color: "#fff", fontWeight: "600" },
  summary: { backgroundColor: "#fff", borderRadius: 14, padding: 16, marginBottom: 12, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  summaryLabel: { color: "#52524e", fontSize: 13, flex: 1 },
  summaryValue: { fontSize: 24, fontWeight: "800" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: "#fff", borderRadius: 12, padding: 14, marginBottom: 8 },
  rowBody: { flex: 1 },
  rowTitle: { fontSize: 16, fontWeight: "600", color: "#1a1a1a" },
  rowMeta: { color: "#52524e", fontSize: 13, marginTop: 2 },
  scoreBox: { alignItems: "flex-end", minWidth: 64 },
  score: { fontSize: 18, fontWeight: "800" },
  letter: { color: "#8c8c87", fontSize: 12, marginTop: 1 },
  empty: { color: "#1a1a1a", fontSize: 16, fontWeight: "600" },
  emptySub: { color: "#8c8c87", textAlign: "center", marginTop: 6 },
});
