import React, { useCallback, useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { deleteMemoryItem, getMemories, loadMemoryItem, MemoriesPage, MemoryDay, MemoryItem, saveMemorySnapshot } from "../lib/api";
import { useQuery } from "../lib/useQuery";
import { useConfirm } from "../components/Confirm";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, EmptyState, ErrorState, Label, Loading, Notice, Screen, T } from "../components/ui";

const RESTORABLE = new Set(["schedule", "schedule_day", "snapshot", "manual_tasks", "identity"]);

/** Account-owned archive, grouped by day, with safe native save/load/delete actions. */
export default function MemoriesScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const confirm = useConfirm();
  const query = useQuery<MemoriesPage>("memories", () => getMemories());
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [olderDays, setOlderDays] = useState<MemoryDay[]>([]);
  const [olderPage, setOlderPage] = useState(1);
  const [moreAvailable, setMoreAvailable] = useState(false);

  React.useEffect(() => {
    if (query.data && olderPage === 1) setMoreAvailable(query.data.has_more);
  }, [olderPage, query.data]);

  const close = useCallback(() => {
    if (router.canGoBack()) router.back();
    else router.replace("/(tabs)/more");
  }, [router]);

  const saveToday = useCallback(async () => {
    if (busy) return;
    setBusy("save");
    setError(null);
    setNotice(null);
    try {
      const now = new Date();
      const date = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
      const result = await saveMemorySnapshot(date);
      if (result.status !== "ok") throw new Error(result.message || "There was nothing to save yet.");
      await query.reload();
      setNotice(`Saved ${result.count || 0} item${result.count === 1 ? "" : "s"} for today.`);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "Couldn't save today.");
    } finally {
      setBusy(null);
    }
  }, [busy, query]);

  const remove = useCallback(async (item: MemoryItem) => {
    const choice = await confirm({
      title: "Delete this memory?",
      message: `“${item.title}” will be removed from your saved timeline.`,
      actions: [{ label: "Delete", destructive: true }, { label: "Cancel", cancel: true }],
    });
    if (choice !== 0 || busy) return;
    setBusy(`delete-${item.id}`);
    setError(null);
    setNotice(null);
    try {
      await deleteMemoryItem(item.id);
      await query.reload();
      setOlderDays((days) => days
        .map((day) => ({ ...day, items: day.items.filter((saved) => saved.id !== item.id), count: day.count - (day.items.some((saved) => saved.id === item.id) ? 1 : 0) }))
        .filter((day) => day.items.length > 0));
      setNotice("Memory deleted.");
    } catch (e: any) {
      setError(e?.message || "Couldn't delete that memory.");
    } finally {
      setBusy(null);
    }
  }, [busy, confirm, query]);

  const loadMore = useCallback(async () => {
    if (busy || !moreAvailable) return;
    setBusy("more");
    setError(null);
    try {
      const next = await getMemories(olderPage + 1);
      setOlderDays((days) => {
        const existing = new Set([...(query.data?.days || []), ...days].map((day) => day.date));
        return [...days, ...next.days.filter((day) => !existing.has(day.date))];
      });
      setOlderPage((page) => page + 1);
      setMoreAvailable(next.has_more);
    } catch (e: any) {
      setError(e?.message || "Couldn't load older memories.");
    } finally {
      setBusy(null);
    }
  }, [busy, moreAvailable, olderPage, query.data?.days]);

  const restore = useCallback(async (item: MemoryItem) => {
    if (!RESTORABLE.has(item.item_type) || busy) return;
    const choice = await confirm({
      title: `Load ${item.title}?`,
      message: item.item_type === "manual_tasks"
        ? "This restores these tasks into your current list. Loading them again may create duplicates."
        : item.item_type === "identity"
          ? "This replaces matching profile and availability fields with the saved values. Current values for those fields will be overwritten."
        : item.item_type.includes("schedule") || item.item_type === "snapshot"
          ? "This makes the saved schedule active and replaces your current active schedule."
          : "This restores the saved information to your account.",
      actions: [{ label: "Load" }, { label: "Cancel", cancel: true }],
    });
    if (choice !== 0) return;
    setBusy(`load-${item.id}`);
    setError(null);
    setNotice(null);
    try {
      const result = await loadMemoryItem(item.id);
      const destination = result.loaded === "identity" ? "/settings" : result.loaded === "manual_tasks" ? "/(tabs)/tasks" : "/(tabs)/plan";
      setNotice("Restored. Opening your workspace…");
      router.push(destination as never);
    } catch (e: any) {
      setError(e?.message || "Couldn't load that memory.");
    } finally {
      setBusy(null);
    }
  }, [busy, confirm, router]);

  return (
    <Screen>
      <View style={{ flexDirection: "row", alignItems: "center", paddingTop: insets.top + space.sm, paddingHorizontal: space.lg, paddingBottom: space.md, borderBottomWidth: 1, borderBottomColor: colors.border }}>
        <Pressable onPress={close} accessibilityRole="button" accessibilityLabel="Close memories" style={{ width: 44, height: 44, marginLeft: -8, alignItems: "center", justifyContent: "center" }}>
          <Ionicons name="arrow-back" size={23} color={colors.textSecondary} />
        </Pressable>
        <View style={{ flex: 1, marginLeft: space.xs }}>
          <T variant="lg" weight="700">Memories</T>
          <T variant="xs" tone="muted">Your saved work, together by day</T>
        </View>
      </View>
      <ScrollView contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: insets.bottom + 100 }} refreshControl={<RefreshControl refreshing={query.refreshing} onRefresh={query.refresh} tintColor={colors.accent} />}>
        {query.stale ? <Notice text="Offline — showing your last loaded memories. Changes need a connection." icon="cloud-offline-outline" /> : null}
        {error ? <Notice text={error} icon="alert-circle-outline" /> : null}
        {notice ? <Notice tone="accent" text={notice} icon="checkmark-circle-outline" /> : null}
        <Card style={{ gap: space.sm }}>
          <T variant="base" weight="700">Keep a record of today</T>
          <T variant="sm" tone="muted">Save your current schedule, tasks, and profile details to revisit later.</T>
          <Button title="Save today" icon="bookmark-outline" busy={busy === "save"} disabled={query.stale || busy !== null} onPress={saveToday} />
        </Card>
        {query.loading && !query.data ? <Loading label="Loading your memories…" /> : null}
        {query.error && !query.data ? <ErrorState message={query.error} onRetry={query.reload} /> : null}
        {!query.loading && !query.error && !query.data?.days.length && !olderDays.length ? (
          <EmptyState icon="bookmark-outline" title="No memories yet" body="Save a schedule or today’s work, and it will show up here." action={<Button title="Plan your week" onPress={() => router.push("/(tabs)/plan")} />} />
        ) : null}
        {[...(query.data?.days || []), ...olderDays].map((day) => <DayGroup key={day.date} day={day} busy={busy} onRestore={restore} onDelete={remove} />)}
        {moreAvailable ? <Button title="Load older memories" icon="chevron-down" kind="secondary" busy={busy === "more"} disabled={busy !== null} onPress={loadMore} /> : null}
      </ScrollView>
    </Screen>
  );
}

function DayGroup({ day, busy, onRestore, onDelete }: { day: MemoryDay; busy: string | null; onRestore: (item: MemoryItem) => void; onDelete: (item: MemoryItem) => void }) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: space.sm }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
        <View style={{ width: 9, height: 9, borderRadius: radius.pill, backgroundColor: colors.accent }} />
        <T variant="base" weight="700" style={{ flex: 1 }}>{day.label}</T>
        <Label>{day.count} saved</Label>
      </View>
      {day.items.map((item, index) => (
        <Card key={item.id} style={{ gap: space.md, marginLeft: space.sm, borderTopWidth: index === 0 ? 0 : 1, borderTopColor: colors.border }}>
          <View style={{ flexDirection: "row", alignItems: "flex-start", gap: space.md }}>
            <View style={{ width: 38, height: 38, borderRadius: radius.md, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" }}>
              <Ionicons name={iconFor(item.item_type)} size={19} color={colors.accent} />
            </View>
            <View style={{ flex: 1, gap: 3 }}>
              <Label>{item.type_label}</Label>
              <T variant="base" weight="700">{item.title}</T>
              <T variant="xs" tone="muted">Saved {item.created_at}</T>
            </View>
          </View>
          <View style={{ flexDirection: "row", gap: space.sm }}>
            {RESTORABLE.has(item.item_type) ? <View style={{ flex: 1 }}><Button title="Load" icon="open-outline" kind="secondary" busy={busy === `load-${item.id}`} disabled={busy !== null} onPress={() => onRestore(item)} /></View> : null}
            <Button title="Delete" icon="trash-outline" kind="ghost" busy={busy === `delete-${item.id}`} disabled={busy !== null} onPress={() => onDelete(item)} />
          </View>
        </Card>
      ))}
    </View>
  );
}

function iconFor(type: string): keyof typeof Ionicons.glyphMap {
  if (type.includes("schedule")) return "calendar-outline";
  if (type === "manual_tasks" || type === "custom_tasks") return "checkbox-outline";
  if (type === "identity") return "person-outline";
  if (type === "resources" || type === "library") return "book-outline";
  if (type === "notes") return "document-text-outline";
  return "bookmark-outline";
}
