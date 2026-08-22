import React, { useCallback, useEffect, useState } from "react";
import { Pressable, ScrollView, Switch, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import Constants from "expo-constants";
import {
  FocusStats,
  getFocusStats,
  getIdentity,
  getQuests,
  getStreak,
  Identity,
  patchIdentity,
  Quest,
  Streak,
} from "../lib/api";
import { API_BASE } from "../lib/config";
import { useQuery } from "../lib/useQuery";
import { useAuth } from "../lib/auth";
import { disablePush, enablePush, isPushEnabled } from "../lib/push";
import { areRemindersEnabled, setRemindersEnabled, syncReminders } from "../lib/reminders";
import { getTasks } from "../lib/api";
import { useTheme, ThemeMode } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, Chip, Divider, Field, Label, Notice, Screen, SegmentedRow, T } from "../components/ui";
import { useConfirm } from "../components/Confirm";

const THEMES: { label: string; value: ThemeMode }[] = [
  { label: "System", value: "system" },
  { label: "Light", value: "light" },
  { label: "Dark", value: "dark" },
];

export default function SettingsScreen() {
  const { colors, mode, setMode } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user, signOut } = useAuth();
  const confirm = useConfirm();

  const streak = useQuery<Streak>("streak", getStreak);
  const identity = useQuery<Identity>("identity", getIdentity);
  const focus = useQuery<{ stats: FocusStats; recent: unknown[] }>("focus-stats", getFocusStats);
  const quests = useQuery<Quest[]>("quests", getQuests);

  const [gradeLevel, setGradeLevel] = useState("");
  const [goals, setGoals] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileNote, setProfileNote] = useState<string | null>(null);

  const [push, setPush] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushNote, setPushNote] = useState<string | null>(null);

  const [local, setLocal] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [localNote, setLocalNote] = useState<string | null>(null);

  // Seeded once the server copy lands. Without the guard, a re-render
  // while the student is typing would overwrite what they just entered.
  const [seeded, setSeeded] = useState(false);
  useEffect(() => {
    if (seeded || !identity.data) return;
    setGradeLevel(String(identity.data.grade_level ?? ""));
    setGoals(String(identity.data.goals ?? ""));
    setSeeded(true);
  }, [identity.data, seeded]);

  useEffect(() => {
    isPushEnabled().then(setPush).catch(() => {});
    areRemindersEnabled().then(setLocal).catch(() => {});
  }, []);

  const togglePush = useCallback(async (on: boolean) => {
    setPushBusy(true);
    setPushNote(null);
    try {
      if (on) {
        const res = await enablePush();
        setPush(res.ok);
        if (!res.ok) setPushNote(res.reason);
      } else {
        await disablePush();
        setPush(false);
      }
    } finally {
      setPushBusy(false);
    }
  }, []);

  const toggleLocal = useCallback(async (on: boolean) => {
    setLocalBusy(true);
    setLocalNote(null);
    try {
      const granted = await setRemindersEnabled(on);
      setLocal(granted);
      if (on && !granted) {
        setLocalNote("Notifications are turned off for IntelliPlan in your phone's settings.");
        return;
      }
      if (granted) {
        // Scheduled from the current list rather than whatever was cached
        // whenever the student last opened the Due tab — turning reminders
        // on should reflect what is actually due now.
        const n = await syncReminders(await getTasks());
        setLocalNote(
          n
            ? `${n} reminder${n === 1 ? "" : "s"} set for the next week.`
            : "Nothing due in the next week to remind you about.",
        );
      }
    } catch (e: any) {
      setLocalNote(e?.message || "Couldn't set those up.");
    } finally {
      setLocalBusy(false);
    }
  }, []);

  const saveProfile = useCallback(async () => {
    setSavingProfile(true);
    setProfileNote(null);
    try {
      await patchIdentity({ grade_level: gradeLevel.trim(), goals: goals.trim() });
      setProfileNote("Saved.");
    } catch (e: any) {
      setProfileNote(e?.message || "Couldn't save that.");
    } finally {
      setSavingProfile(false);
    }
  }, [gradeLevel, goals]);

  const confirmSignOut = useCallback(async () => {
    const choice = await confirm({
      title: "Sign out?",
      message: "You'll need your email and password to get back in.",
      actions: [
        { label: "Sign out", destructive: true },
        { label: "Cancel", cancel: true },
      ],
    });
    if (choice !== 0) return;
    await signOut();
    router.replace("/login");
  }, [signOut, router, confirm]);

  const s = streak.data;
  const version = Constants.expoConfig?.version ?? "—";

  return (
    <Screen>
      <View
        style={{
          flexDirection: "row",
          alignItems: "center",
          paddingTop: insets.top + space.sm,
          paddingHorizontal: space.lg,
          paddingBottom: space.md,
          borderBottomWidth: 1,
          borderBottomColor: colors.border,
        }}
      >
        <T variant="lg" weight="700" style={{ flex: 1 }}>
          You
        </T>
        <Pressable onPress={() => router.back()} hitSlop={10} accessibilityRole="button" accessibilityLabel="Close">
          <Ionicons name="close" size={26} color={colors.textSecondary} />
        </Pressable>
      </View>

      <ScrollView
        contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: insets.bottom + 40 }}
        keyboardShouldPersistTaps="handled"
      >
        {/* ── Account ── */}
        <Card style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
          <View
            style={{
              width: 48,
              height: 48,
              borderRadius: radius.pill,
              backgroundColor: colors.accent,
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <T variant="md" weight="700" tone="onAccent">
              {(user?.name || user?.email || "?").charAt(0).toUpperCase()}
            </T>
          </View>
          <View style={{ flex: 1 }}>
            <T variant="base" weight="600" numberOfLines={1}>
              {user?.name || user?.email?.split("@")[0] || "Signed in"}
            </T>
            <T variant="sm" tone="muted" numberOfLines={1}>
              {user?.email}
            </T>
          </View>
        </Card>

        {/* ── Streak ── */}
        {s ? (
          <Card style={{ gap: space.md }}>
            <Label>Momentum</Label>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm }}>
              <Chip
                label={`${s.streak_count ?? 0} day streak`}
                icon="flame"
                fg={colors.warnText}
                bg={colors.warnSoft}
              />
              <Chip label={`${s.spark_balance ?? 0} sparks`} icon="sparkles" />
              <Chip label={`Level ${s.level ?? 1}${s.level_title ? ` · ${s.level_title}` : ""}`} icon="trophy-outline" />
              <Chip
                label={`${s.streak_freeze_count ?? 0}/${s.freeze_capacity ?? 0} freezes`}
                icon="snow-outline"
              />
            </View>
            {s.at_risk ? (
              <Notice text="Your streak is at risk today — finish one thing to keep it." icon="flame-outline" />
            ) : null}
          </Card>
        ) : null}

        {/* ── Connected platforms ── */}
        <Pressable onPress={() => router.push("/connect")} accessibilityRole="button">
          <Card style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
            <View
              style={{
                width: 40,
                height: 40,
                borderRadius: radius.md,
                backgroundColor: colors.accentSoft,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Ionicons name="school-outline" size={19} color={colors.accent} />
            </View>
            <View style={{ flex: 1 }}>
              <T variant="base" weight="600">
                Your school
              </T>
              <T variant="sm" tone="muted">
                Connect Canvas, StudentVue, Schoology and more
              </T>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textMuted} />
          </Card>
        </Pressable>

        {/* ── Focus history ──
            Shown as plain counts rather than a chart: with a handful of
            sessions a sparkline is noise pretending to be a trend. */}
        {focus.data?.stats?.sessions ? (
          <Card style={{ gap: space.md }}>
            <Label>Focus sessions</Label>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm }}>
              <Chip label={`${focus.data.stats.sessions} sessions`} icon="timer-outline" />
              <Chip
                label={`${Math.round(focus.data.stats.total_minutes / 60)}h focused`}
                icon="hourglass-outline"
              />
              {focus.data.stats.median_minutes ? (
                <Chip label={`${focus.data.stats.median_minutes}m typical`} />
              ) : null}
              {focus.data.stats.completion_rate !== null ? (
                <Chip
                  label={`${Math.round((focus.data.stats.completion_rate ?? 0) * 100)}% finished`}
                  icon="checkmark-circle-outline"
                  fg={colors.ok}
                  bg={colors.okSoft}
                />
              ) : null}
            </View>
          </Card>
        ) : null}

        {/* ── Weekly quests ── */}
        {quests.data?.length ? (
          <Card style={{ gap: space.md }}>
            <Label>This week's quests</Label>
            {quests.data.slice(0, 4).map((qu, i) => {
              const target = Number(qu.target ?? qu.goal ?? 0);
              const progress = Number(qu.progress ?? 0);
              const pct = target > 0 ? Math.min(1, progress / target) : 0;
              const doneQ = !!qu.complete || (target > 0 && progress >= target);
              return (
                <View key={String(qu.id ?? qu.key ?? i)} style={{ gap: 6 }}>
                  <View style={{ flexDirection: "row", justifyContent: "space-between", gap: space.sm }}>
                    <T variant="sm" weight="600" style={{ flex: 1 }} numberOfLines={2}>
                      {String(qu.title || qu.label || qu.description || "Quest")}
                    </T>
                    <T variant="sm" tone={doneQ ? "ok" : "muted"} weight="600">
                      {target > 0 ? `${Math.min(progress, target)}/${target}` : ""}
                      {qu.reward ? ` · ${qu.reward}✦` : ""}
                    </T>
                  </View>
                  {target > 0 ? (
                    <View
                      style={{
                        height: 5,
                        borderRadius: radius.pill,
                        backgroundColor: colors.bgElevated,
                        overflow: "hidden",
                      }}
                    >
                      <View
                        style={{
                          width: `${pct * 100}%`,
                          height: "100%",
                          backgroundColor: doneQ ? colors.ok : colors.accent,
                        }}
                      />
                    </View>
                  ) : null}
                </View>
              );
            })}
          </Card>
        ) : null}

        {/* ── Learning profile ── */}
        <Card style={{ gap: space.md }}>
          <View>
            <Label>Learning profile</Label>
            <T variant="sm" tone="secondary" style={{ marginTop: 4 }}>
              Plani and the scheduler both read this, so keeping it current changes
              what you get back.
            </T>
          </View>
          <Field
            label="Grade level"
            placeholder="e.g. 11th grade"
            value={gradeLevel}
            onChangeText={setGradeLevel}
          />
          <Field
            label="Goals"
            placeholder="What are you working toward this term?"
            value={goals}
            onChangeText={setGoals}
            multiline
          />
          {profileNote ? (
            <Notice
              text={profileNote}
              tone={profileNote === "Saved." ? "accent" : "warn"}
              icon={profileNote === "Saved." ? "checkmark-circle-outline" : "alert-circle-outline"}
            />
          ) : null}
          <Button title="Save profile" kind="secondary" busy={savingProfile} onPress={saveProfile} />
        </Card>

        {/* ── Notifications ──
            Two switches, not one, because they are genuinely different
            things and collapsing them would mean the toggle silently does
            nothing in Expo Go. */}
        <Card style={{ gap: space.md }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
            <View style={{ flex: 1 }}>
              <Label>Deadline reminders</Label>
              <T variant="sm" tone="secondary" style={{ marginTop: 4 }}>
                Your phone reminds you the evening before something is due. Works
                offline, and in Expo Go.
              </T>
            </View>
            <Switch
              value={local}
              onValueChange={toggleLocal}
              disabled={localBusy}
              trackColor={{ true: colors.accent, false: colors.bgElevated }}
            />
          </View>
          {localNote ? <Notice text={localNote} tone="accent" icon="information-circle-outline" /> : null}

          <Divider />

          <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
            <View style={{ flex: 1 }}>
              <Label>Server nudges</Label>
              <T variant="sm" tone="secondary" style={{ marginTop: 4 }}>
                IntelliPlan pushes when a streak is at risk or a deadline moves,
                even with the app closed. Needs a development build.
              </T>
            </View>
            <Switch
              value={push}
              onValueChange={togglePush}
              disabled={pushBusy}
              trackColor={{ true: colors.accent, false: colors.bgElevated }}
            />
          </View>
          {pushNote ? <Notice text={pushNote} icon="information-circle-outline" /> : null}
        </Card>

        {/* ── Appearance ── */}
        <Card style={{ gap: space.sm }}>
          <Label>Appearance</Label>
          <SegmentedRow options={THEMES} value={mode} onChange={setMode} />
        </Card>

        {/* ── About ── */}
        <Card style={{ gap: 6 }}>
          <Label>Connection</Label>
          <T variant="sm" tone="secondary">
            {API_BASE}
          </T>
          <T variant="xs" tone="muted">
            IntelliPlan {version} · signed in as {user?.email}
          </T>
        </Card>

        <Button title="Sign out" kind="danger" icon="log-out-outline" onPress={confirmSignOut} />
      </ScrollView>
    </Screen>
  );
}
