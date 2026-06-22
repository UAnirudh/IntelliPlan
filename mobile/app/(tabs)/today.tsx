import { useEffect, useState } from "react";
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
import { apiFetch } from "../../lib/api";

type Task = { id: string; title: string; course?: string; due_date?: string };
type TodayPayload = { plan?: Task[]; brief?: { headline?: string; body?: string } };

export default function TodayScreen() {
  const [data, setData] = useState<TodayPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const json = await apiFetch<TodayPayload>("/api/today");
      setData(json);
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Failed to load today.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => { load(); }, []);

  if (loading) return <View style={styles.center}><ActivityIndicator /></View>;
  if (error) return <View style={styles.center}><Text style={styles.error}>{error}</Text></View>;

  return (
    <View style={styles.wrap}>
      {data?.brief?.headline ? (
        <View style={styles.brief}>
          <Text style={styles.briefTitle}>{data.brief.headline}</Text>
          {data.brief.body ? <Text style={styles.briefBody}>{data.brief.body}</Text> : null}
        </View>
      ) : null}
      <FlatList
        data={data?.plan || []}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        ListEmptyComponent={<Text style={styles.empty}>You're all caught up.</Text>}
        renderItem={({ item }) => (
          <View style={styles.row}>
            <Text style={styles.rowTitle}>{item.title}</Text>
            <Text style={styles.rowMeta}>{item.course || "—"} · {item.due_date || "no due date"}</Text>
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, padding: 16, backgroundColor: "#f5f4f1" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  error: { color: "#c62828" },
  brief: { backgroundColor: "#fff", borderRadius: 14, padding: 16, marginBottom: 12 },
  briefTitle: { fontSize: 18, fontWeight: "700", color: "#1a1a1a" },
  briefBody: { color: "#52524e", marginTop: 4 },
  row: { backgroundColor: "#fff", borderRadius: 12, padding: 14, marginBottom: 8 },
  rowTitle: { fontSize: 16, fontWeight: "600", color: "#1a1a1a" },
  rowMeta: { color: "#52524e", fontSize: 13, marginTop: 2 },
  empty: { textAlign: "center", color: "#8c8c87", marginTop: 32 },
});
