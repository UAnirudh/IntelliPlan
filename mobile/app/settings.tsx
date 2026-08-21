import React, { useCallback, useEffect, useState } from "react";
import { Alert, Pressable, ScrollView, Switch, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import Constants from "expo-constants";
import { getIdentity, getStreak, Identity, patchIdentity, Streak } from "../lib/api";
import { API_BASE } from "../lib/config";
import { useQuery } from "../lib/useQuery";
import { useAuth } from "../lib/auth";
import { disablePush, enablePush, isPushEnabled } from "../lib/push";
import { useTheme, ThemeMode } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, Chip, Field, Label, Notice, Screen, SegmentedRow, T } from "../components/ui";

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

  const streak = useQuery<Streak>("streak", getStreak);
  const identity = useQuery<Identity>("identity", getIdentity);

  const [gradeLevel, setGradeLevel] = useState("");
  const [goals, setGoals] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileNote, setProfileNote] = useState<string | null>(null);

  const [push, setPush] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushNote, setPushNote] = useState<string | null>(null);

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

  const confirmSignOut = useCallback(() => {
    Alert.alert("Sign out?", "You'll need your email and password to get back in.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Sign out",
        style: "destructive",
        onPress: async () => {
          await signOut();
          router.replace("/login");
        },
      },
    ]);
  }, [signOut, router]);

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

        {/* ── Notifications ── */}
        <Card style={{ gap: space.md }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
            <View style={{ flex: 1 }}>
              <Label>Deadline reminders</Label>
              <T variant="sm" tone="secondary" style={{ marginTop: 4 }}>
                Push a nudge before something is due and when a streak is at risk.
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
