import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator, Alert, Pressable, RefreshControl, ScrollView,
  StyleSheet, Text, View,
} from "react-native";
import { generateSchedule, getSchedule } from "../../lib/api";

/**
 * The study plan the student already has.
 *
 * Reads the *saved* plan rather than generating one on open. Regenerating
 * every launch would silently throw away the progress ticked off against
 * the existing plan, and would burn an AI call to produce something the
 * student did not ask for. Generating is a button, and only offered when
 * there is nothing saved.
 */

type Block = {
  title?: string;
  course?: string;
  time_slot?: string;
  start?: string;
  end?: string;
  duration?: number | string;
  kind?: string;
};
type Day = { day?: string; date?: string; blocks?: Block[] };

export default function ScheduleScreen() {
  const [days, setDays] = useState<Day[]>([]);
  const [meta, setMeta] = useState<{ name?: string; created_at?: string } | null>(null);
  const [hasPlan, setHasPlan] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await getSchedule();
      // "none" is a legitimate state, not a failure: a student who has
      // never generated a plan should be offered one, not shown an error.
      if (!d || d.status === "none") {
        setHasPlan(false);
        setDays([]);
        setMeta(null);
      } else {
        setHasPlan(true);
        setDays(Array.isArray(d?.data?.schedule) ? d.data.schedule : []);
        setMeta({ name: d.name, created_at: d.created_at });
      }
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Could not load your schedule.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function build() {
    setBusy(true);
    try {
      // Defaults match the web scheduler's own defaults. Asking a student
      // to configure hours before they have ever seen a plan is a worse
      // first run than showing one they can regenerate.
      await generateSchedule(2, "evening");
      await load();
    } catch (e: any) {
      Alert.alert("Could not build a plan", e?.message || "The scheduler did not respond.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <View style={styles.center}><ActivityIndicator /></View>;

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.error}>{error}</Text>
        <Pressable style={styles.btn} onPress={() => { setLoading(true); load(); }}>
          <Text style={styles.btnText}>Try again</Text>
        </Pressable>
      </View>
    );
  }

  if (!hasPlan) {
    return (
      <View style={styles.center}>
        <Text style={styles.empty}>No study plan yet.</Text>
        <Text style={styles.emptySub}>
          Build one from what is currently due across your connected platforms.
        </Text>
        <Pressable style={[styles.btn, busy && styles.btnBusy]} onPress={build} disabled={busy}>
          {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Build my plan</Text>}
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.wrap}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />
      }
    >
      {meta?.created_at ? (
        <Text style={styles.meta}>
          {meta.name || "Your plan"} · saved {meta.created_at}
        </Text>
      ) : null}

      {days.length === 0 ? (
        <Text style={styles.emptySub}>This plan has no blocks in it.</Text>
      ) : days.map((day, di) => (
        <View key={`${day.day || day.date || di}`} style={styles.day}>
          <Text style={styles.dayTitle}>{day.day || day.date || `Day ${di + 1}`}</Text>
          {(day.blocks || []).length === 0 ? (
            <Text style={styles.rest}>Nothing scheduled.</Text>
          ) : (day.blocks || []).map((b, bi) => (
            <View key={bi} style={styles.block}>
              <Text style={styles.blockTime}>
                {b.time_slot || [b.start, b.end].filter(Boolean).join("–") || "—"}
              </Text>
              <View style={styles.blockBody}>
                <Text style={styles.blockTitle} numberOfLines={2}>{b.title || "Study"}</Text>
                {b.course ? <Text style={styles.blockMeta}>{b.course}</Text> : null}
              </View>
            </View>
          ))}
        </View>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: "#f5f4f1" },
  content: { padding: 16 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, backgroundColor: "#f5f4f1" },
  error: { color: "#c62828", textAlign: "center" },
  meta: { color: "#8c8c87", fontSize: 12, marginBottom: 10 },
  day: { marginBottom: 16 },
  dayTitle: { fontSize: 15, fontWeight: "700", color: "#1a1a1a", marginBottom: 6 },
  block: { flexDirection: "row", gap: 12, backgroundColor: "#fff", borderRadius: 12, padding: 14, marginBottom: 8 },
  blockTime: { color: "#1a56db", fontWeight: "700", fontSize: 13, minWidth: 92 },
  blockBody: { flex: 1 },
  blockTitle: { fontSize: 15, fontWeight: "600", color: "#1a1a1a" },
  blockMeta: { color: "#52524e", fontSize: 13, marginTop: 2 },
  rest: { color: "#8c8c87", fontSize: 13, paddingLeft: 2 },
  empty: { color: "#1a1a1a", fontSize: 16, fontWeight: "600" },
  emptySub: { color: "#8c8c87", textAlign: "center", marginTop: 6, marginBottom: 16 },
  btn: { marginTop: 12, paddingVertical: 12, paddingHorizontal: 24, borderRadius: 999, backgroundColor: "#1a1a1a", minWidth: 160, alignItems: "center" },
  btnBusy: { opacity: 0.7 },
  btnText: { color: "#fff", fontWeight: "600", fontSize: 15 },
});
