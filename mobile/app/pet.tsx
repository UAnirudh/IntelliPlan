import React, { useCallback, useEffect, useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { careForPet, getPetStatus, openPetChest, PetStatus, renamePet, visitPet } from "../lib/api";
import { useQuery } from "../lib/useQuery";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, ErrorState, Field, Label, Loading, Notice, Screen, T } from "../components/ui";

const CARE_ORDER = ["feed", "play", "pet", "study_with"];
const MOOD_LABELS: Record<string, string> = {
  joyful: "Feeling great", happy: "Happy to see you", neutral: "Ready to hang out", sleepy: "A little sleepy",
};

/** Native companion view with server-owned XP, cooldowns, stages and daily rewards. */
export default function PetScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const query = useQuery<PetStatus>("pet-status", getPetStatus);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");

  useEffect(() => {
    let active = true;
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    visitPet(timezone).then((result) => {
      if (active && result.awarded?.delta) query.reload().catch(() => {});
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  const close = useCallback(() => {
    if (router.canGoBack()) router.back();
    else router.replace("/(tabs)/more");
  }, [router]);

  const care = useCallback(async (action: string) => {
    if (busy) return;
    setBusy(action);
    setError(null);
    setNotice(null);
    try {
      const result = await careForPet(action);
      await query.reload();
      setNotice(result.copy || `Plani gained ${result.xp_awarded || 0} XP.`);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "Couldn't complete that action.");
    } finally {
      setBusy(null);
    }
  }, [busy, query]);

  const claimChest = useCallback(async () => {
    if (busy) return;
    setBusy("chest");
    setError(null);
    setNotice(null);
    try {
      const result = await openPetChest();
      await query.reload();
      setNotice(result.status === "already_claimed"
        ? "You already opened today's chest. Come back tomorrow for another."
        : `Daily chest opened — ${result.chest?.xp || 0} XP added.`);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "Couldn't open the daily chest.");
    } finally {
      setBusy(null);
    }
  }, [busy, query]);

  const saveName = useCallback(async () => {
    if (!name.trim() || busy) {
      if (!name.trim()) setError("Give Plani a name first.");
      return;
    }
    setBusy("rename");
    setError(null);
    try {
      await renamePet(name.trim().slice(0, 40));
      await query.reload();
      setRenaming(false);
      setName("");
      setNotice("Name saved.");
    } catch (e: any) {
      setError(e?.message || "Couldn't save that name.");
    } finally {
      setBusy(null);
    }
  }, [busy, name, query]);

  const header = (
    <View style={{ flexDirection: "row", alignItems: "center", paddingTop: insets.top + space.sm, paddingHorizontal: space.lg, paddingBottom: space.md, borderBottomWidth: 1, borderBottomColor: colors.border }}>
      <Pressable onPress={close} accessibilityRole="button" accessibilityLabel="Close Plani's pet" style={{ width: 44, height: 44, marginLeft: -8, alignItems: "center", justifyContent: "center" }}>
        <Ionicons name="arrow-back" size={23} color={colors.textSecondary} />
      </Pressable>
      <View style={{ flex: 1, marginLeft: space.xs }}>
        <T variant="lg" weight="700">My Pet</T>
        <T variant="xs" tone="muted">A little motivation to keep your streak going</T>
      </View>
    </View>
  );

  if (query.loading && !query.data) return <Screen>{header}<Loading label="Meeting Plani…" /></Screen>;
  if (query.error && !query.data) return <Screen>{header}<ErrorState message={query.error} onRetry={query.reload} /></Screen>;

  const pet = query.data;
  const progress = Math.max(0, Math.min(1, Number(pet?.progress_to_next || 0)));

  return (
    <Screen>
      {header}
      <ScrollView contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: insets.bottom + 100 }} refreshControl={<RefreshControl refreshing={query.refreshing} onRefresh={query.refresh} tintColor={colors.accent} />}>
        {query.stale ? <Notice text="Offline — showing Plani's last status. Care actions need a connection." icon="cloud-offline-outline" /> : null}
        {error ? <Notice text={error} icon="alert-circle-outline" /> : null}
        {notice ? <Notice tone="accent" text={notice} icon="heart-outline" /> : null}

        <Card style={{ alignItems: "center", gap: space.md, paddingVertical: space.xl }}>
          <View style={{ width: 112, height: 112, borderRadius: radius.pill, backgroundColor: colors.accentSoft, borderWidth: 2, borderColor: colors.borderAccent, alignItems: "center", justifyContent: "center" }}>
            <Ionicons name="sparkles" size={50} color={colors.accent} />
          </View>
          <View style={{ alignItems: "center", gap: 3 }}>
            <Label>{pet?.stage_name || "Egg"} stage · Level {pet?.level || 1}</Label>
            <T variant="xl" weight="700">{pet?.name || "Plani"}</T>
            <T variant="sm" tone="muted">{MOOD_LABELS[pet?.mood || ""] || "Ready to see you"}</T>
          </View>
          <Pressable onPress={() => { setName(pet?.name || "Plani"); setRenaming(true); }} accessibilityRole="button" style={{ minHeight: 44, justifyContent: "center", paddingHorizontal: space.md }}>
            <T tone="accent" weight="600">Rename Plani</T>
          </Pressable>
          {renaming ? (
            <View style={{ width: "100%", gap: space.sm }}>
              <Field label="Name" value={name} onChangeText={setName} maxLength={40} autoFocus returnKeyType="done" onSubmitEditing={saveName} />
              <View style={{ flexDirection: "row", gap: space.sm }}>
                <View style={{ flex: 1 }}><Button title="Save name" busy={busy === "rename"} onPress={saveName} /></View>
                <Button title="Cancel" kind="secondary" disabled={busy === "rename"} onPress={() => setRenaming(false)} />
              </View>
            </View>
          ) : null}
        </Card>

        <Card style={{ gap: space.md }}>
          <View style={{ flexDirection: "row", alignItems: "center" }}>
            <T variant="base" weight="700" style={{ flex: 1 }}>Growing together</T>
            <T variant="xs" tone="muted">{pet?.xp || 0} XP</T>
          </View>
          <View accessibilityRole="progressbar" accessibilityLabel="Progress to next pet stage" accessibilityValue={{ min: 0, max: 100, now: Math.round(progress * 100) }} style={{ height: 10, backgroundColor: colors.bgSecondary, borderRadius: radius.pill, overflow: "hidden" }}>
            <View style={{ width: `${Math.round(progress * 100)}%`, height: "100%", backgroundColor: colors.accent, borderRadius: radius.pill }} />
          </View>
          <T variant="xs" tone="muted">{pet?.xp_for_next_stage ? `${pet.xp_into_stage || 0} / ${pet.xp_for_next_stage} XP to the next stage` : "Fully grown — keep learning together."}</T>
          <View style={{ flexDirection: "row", gap: space.sm }}>
            <Stat label="Your streak" value={`${pet?.streak || 0} days`} />
            <Stat label="Longest" value={`${pet?.longest_streak || 0} days`} />
          </View>
        </Card>

        <Card style={{ gap: space.md }}>
          <View>
            <T variant="base" weight="700">Spend a moment together</T>
            <T variant="xs" tone="muted">Each activity earns XP. Plani's cooldowns keep rewards fair.</T>
          </View>
          {CARE_ORDER.map((action) => {
            const item = pet?.care?.[action];
            if (!item) return null;
            return <View key={action} style={{ flexDirection: "row", alignItems: "center", gap: space.sm, paddingTop: space.sm, borderTopWidth: 1, borderTopColor: colors.border }}>
              <View style={{ width: 38, height: 38, borderRadius: radius.md, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" }}>
                <T variant="base">{item.emoji}</T>
              </View>
              <View style={{ flex: 1 }}>
                <T variant="sm" weight="600">{item.label}</T>
                <T variant="xs" tone="muted">+{item.xp} XP{item.ready ? "" : ` · Ready in ${Math.max(1, Math.ceil(item.wait_seconds / 60))} min`}</T>
              </View>
              <Button title={item.ready ? "Do it" : "Later"} busy={busy === action} kind={item.ready ? "secondary" : "ghost"} disabled={!item.ready || busy !== null || query.stale} onPress={() => care(action)} style={{ minHeight: 42, paddingVertical: 8, paddingHorizontal: space.md }} />
            </View>;
          })}
        </Card>

        <Card style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
          <View style={{ width: 52, height: 52, borderRadius: radius.lg, backgroundColor: pet?.chest.ready ? colors.accentSoft : colors.bgSecondary, alignItems: "center", justifyContent: "center" }}>
            <Ionicons name={pet?.chest.ready ? "gift-outline" : "checkmark-circle-outline"} size={25} color={pet?.chest.ready ? colors.accent : colors.textMuted} />
          </View>
          <View style={{ flex: 1, gap: 2 }}>
            <T variant="base" weight="700">Daily chest</T>
            <T variant="xs" tone="muted">{pet?.chest.ready ? `Claim +${pet?.chest.next_reward?.xp || 0} XP` : `Come back tomorrow · ${pet?.chest.current_streak_days || 0}-day chest streak`}</T>
          </View>
          {pet?.chest.ready ? <Button title="Open" icon="gift-outline" busy={busy === "chest"} disabled={busy !== null || query.stale} onPress={claimChest} style={{ paddingHorizontal: space.md }} /> : null}
        </Card>

        <Card style={{ gap: space.sm }}>
          <T variant="base" weight="700">Growth stages</T>
          {(pet?.stages || []).map((stage, index) => {
            const currentIndex = (pet?.stages || []).findIndex((row) => row.id === pet?.stage_id);
            const unlocked = index <= currentIndex;
            const current = stage.id === pet?.stage_id;
            return <View key={stage.id} style={{ flexDirection: "row", alignItems: "center", gap: space.sm, paddingVertical: space.sm, borderTopWidth: index ? 1 : 0, borderTopColor: colors.border, opacity: unlocked ? 1 : 0.62 }}>
              <Ionicons name={current ? "sparkles" : unlocked ? "checkmark-circle" : "lock-closed-outline"} size={18} color={current ? colors.accent : unlocked ? colors.ok : colors.textMuted} />
              <View style={{ flex: 1 }}>
                <T variant="sm" weight="600">{stage.name}</T>
                <T variant="xs" tone="muted">{stage.title}</T>
              </View>
              <T variant="xs" tone={current ? "accent" : "muted"}>{current ? "Plani" : unlocked ? "Reached" : `${stage.min_xp} XP`}</T>
            </View>;
          })}
        </Card>
      </ScrollView>
    </Screen>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  const { colors } = useTheme();
  return <View style={{ flex: 1, padding: space.sm, borderRadius: radius.md, backgroundColor: colors.bgSecondary }}>
    <T variant="xs" tone="muted">{label}</T>
    <T variant="base" weight="700">{value}</T>
  </View>;
}
