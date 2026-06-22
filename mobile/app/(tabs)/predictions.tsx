import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { apiFetch } from "../../lib/api";

type Prediction = {
  course: string;
  current_percent: number | null;
  predicted_percent: number;
  confidence: number;
  trend: "improving" | "steady" | "slipping";
  narrative: string;
  sample_size: number;
};

export default function PredictionsScreen() {
  const [items, setItems] = useState<Prediction[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const json = await apiFetch<{ predictions: Prediction[]; message?: string }>("/api/grade-predictions", {
        method: "POST",
        body: JSON.stringify({}),
      });
      setItems(json.predictions || []);
      if (!json.predictions?.length && json.message) setError(json.message);
    } catch (e: any) {
      setError(e?.message || "Prediction failed.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { run(); }, []);

  return (
    <ScrollView style={styles.wrap} contentContainerStyle={{ padding: 16 }}>
      <View style={styles.head}>
        <Text style={styles.title}>Grade predictions</Text>
        <Pressable onPress={run} disabled={busy} style={styles.btn}>
          {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Refresh</Text>}
        </Pressable>
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {items.map((p) => (
        <View key={p.course} style={[styles.card, trendStyle(p.trend)]}>
          <Text style={styles.course}>{p.course.toUpperCase()}</Text>
          <Text style={styles.percent}>{p.predicted_percent}%</Text>
          <Text style={styles.narrative}>{p.narrative}</Text>
          <View style={styles.metaRow}>
            <Text style={styles.pill}>{p.trend}</Text>
            <Text style={styles.pill}>{p.sample_size} graded</Text>
            <Text style={styles.pill}>{Math.round(p.confidence * 100)}% conf</Text>
          </View>
        </View>
      ))}
    </ScrollView>
  );
}

function trendStyle(trend: Prediction["trend"]) {
  if (trend === "improving") return { borderLeftColor: "#0a7558" };
  if (trend === "slipping") return { borderLeftColor: "#c62828" };
  return { borderLeftColor: "#1a56db" };
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: "#f5f4f1" },
  head: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 12 },
  title: { fontSize: 22, fontWeight: "700", color: "#1a1a1a" },
  btn: { backgroundColor: "#1a1a1a", paddingHorizontal: 16, paddingVertical: 10, borderRadius: 999 },
  btnText: { color: "#fff", fontWeight: "600" },
  card: { backgroundColor: "#fff", borderRadius: 12, padding: 16, marginBottom: 10, borderLeftWidth: 4 },
  course: { fontSize: 12, color: "#8c8c87", letterSpacing: 0.5, fontWeight: "600" },
  percent: { fontSize: 28, fontWeight: "700", color: "#1a1a1a", marginVertical: 2 },
  narrative: { fontSize: 13, color: "#52524e", lineHeight: 18 },
  metaRow: { flexDirection: "row", gap: 6, marginTop: 8, flexWrap: "wrap" },
  pill: { fontSize: 11, color: "#52524e", backgroundColor: "#f5f4f1", paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999 },
  error: { color: "#c62828", marginBottom: 12 },
});
