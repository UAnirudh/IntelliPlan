import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Alert, FlatList, Pressable, RefreshControl,
  StyleSheet, Text, View,
} from "react-native";
import { Assignment, dismissAssignment, getAssignments, restoreAssignment } from "../../lib/api";

/**
 * Everything due, from every platform the student connected.
 *
 * The list comes from /api/v1/assignments, which the server resolves
 * through the same view the website renders — so Canvas, StudentVue,
 * Schoology, Classroom, Blackboard and Moodle all arrive merged, and a
 * newly connected platform shows up here without the app knowing it
 * exists.
 */

const PRIORITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

function dueLabel(due?: string): string {
  if (!due) return "No due date";
  const parsed = new Date(due);
  if (Number.isNaN(parsed.getTime())) return due;   // server sends prose sometimes
  const today = new Date();
  const days = Math.round(
    (new Date(parsed.toDateString()).getTime() - new Date(today.toDateString()).getTime())
    / 86_400_000,
  );
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  if (days < 0) return `${Math.abs(days)}d overdue`;
  return `Due in ${days}d`;
}

export default function AssignmentsScreen() {
  const [items, setItems] = useState<Assignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  //: Titles currently being written to the server. Keyed rather than a
  //: single boolean so ticking one row does not freeze every other row.
  const [pending, setPending] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      setItems(await getAssignments());
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Could not load assignments.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const sorted = useMemo(() => {
    // Overdue first, then by due date, then by priority. A flat server
    // order buries the thing that is actually late.
    return [...items].sort((a, b) => {
      const ad = a.due_date ? Date.parse(a.due_date) : Number.MAX_SAFE_INTEGER;
      const bd = b.due_date ? Date.parse(b.due_date) : Number.MAX_SAFE_INTEGER;
      if (ad !== bd) return ad - bd;
      const ap = PRIORITY_ORDER[String(a.priority || "").toLowerCase()] ?? 1;
      const bp = PRIORITY_ORDER[String(b.priority || "").toLowerCase()] ?? 1;
      return ap - bp;
    });
  }, [items]);

  async function toggle(item: Assignment) {
    const title = item.title;
    if (!title || pending[title]) return;
    const wasDismissed = !!item.dismissed;

    // Optimistic: the row flips immediately and rolls back if the write
    // fails. Waiting on a round-trip to acknowledge a tick feels broken on
    // a phone, where the connection is the least reliable part.
    setPending((p) => ({ ...p, [title]: true }));
    setItems((list) => list.map((a) => (a.title === title ? { ...a, dismissed: !wasDismissed } : a)));

    try {
      if (wasDismissed) await restoreAssignment(title);
      else await dismissAssignment(title);
    } catch (e: any) {
      setItems((list) => list.map((a) => (a.title === title ? { ...a, dismissed: wasDismissed } : a)));
      Alert.alert("Could not save", e?.message || "That change did not reach the server.");
    } finally {
      setPending((p) => {
        const next = { ...p };
        delete next[title];
        return next;
      });
    }
  }

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
      <FlatList
        data={sorted}
        keyExtractor={(item, i) => `${item.title}-${i}`}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />
        }
        ListEmptyComponent={
          <View style={styles.center}>
            <Text style={styles.empty}>Nothing due.</Text>
            <Text style={styles.emptySub}>
              If you expected work here, connect a school platform on the website first.
            </Text>
          </View>
        }
        renderItem={({ item }) => {
          const busy = !!pending[item.title];
          const done = !!item.dismissed;
          return (
            <Pressable
              style={[styles.row, done && styles.rowDone]}
              onPress={() => toggle(item)}
              disabled={busy}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: done, disabled: busy }}
              accessibilityLabel={`${item.title}, ${done ? "done" : "not done"}`}
            >
              <View style={[styles.check, done && styles.checkOn]}>
                {busy ? <ActivityIndicator size="small" color={done ? "#fff" : "#1a56db"} />
                      : done ? <Text style={styles.tick}>✓</Text> : null}
              </View>
              <View style={styles.rowBody}>
                <Text style={[styles.rowTitle, done && styles.strike]} numberOfLines={2}>
                  {item.title}
                </Text>
                <Text style={styles.rowMeta}>
                  {(item.course || "No course")} · {dueLabel(item.due_date)}
                  {item.platform ? ` · ${item.platform}` : ""}
                </Text>
              </View>
            </Pressable>
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
  row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: "#fff", borderRadius: 12, padding: 14, marginBottom: 8 },
  rowDone: { opacity: 0.55 },
  rowBody: { flex: 1 },
  rowTitle: { fontSize: 16, fontWeight: "600", color: "#1a1a1a" },
  strike: { textDecorationLine: "line-through" },
  rowMeta: { color: "#52524e", fontSize: 13, marginTop: 2 },
  check: { width: 26, height: 26, borderRadius: 8, borderWidth: 2, borderColor: "#c9c7c1", alignItems: "center", justifyContent: "center" },
  checkOn: { backgroundColor: "#1a56db", borderColor: "#1a56db" },
  tick: { color: "#fff", fontWeight: "800", fontSize: 15 },
  empty: { color: "#1a1a1a", fontSize: 16, fontWeight: "600" },
  emptySub: { color: "#8c8c87", textAlign: "center", marginTop: 6 },
});
