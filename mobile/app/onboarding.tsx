import React, { useCallback, useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as WebBrowser from "expo-web-browser";
import { getProviders, patchIdentity, Provider } from "../lib/api";
import { API_BASE } from "../lib/config";
import { useAuth } from "../lib/auth";
import { markOnboarded } from "../lib/onboarding";
import { setRemindersEnabled } from "../lib/reminders";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, Chip, Field, Label, Notice, Screen, T } from "../components/ui";

/**
 * First run.
 *
 * Without this, a student who has just signed up lands on five tabs that
 * are all empty and are all empty *for the same reason* — nothing is
 * connected yet — with nothing anywhere saying so. That reads as a broken
 * app rather than an unconfigured one.
 *
 * Three steps, and every one of them is skippable. The app works without
 * any of them (manual tasks, Plani, the timer), so blocking entry on a
 * Canvas login would be charging admission for a door that is already
 * open.
 */
type Step = 0 | 1 | 2;

export default function OnboardingScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();

  const [step, setStep] = useState<Step>(0);
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [gradeLevel, setGradeLevel] = useState("");
  const [goals, setGoals] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  // Refreshed on focus so returning from the browser shows the connection
  // that was just made — that is the moment the step's whole point lands.
  useFocusEffect(
    useCallback(() => {
      getProviders()
        .then(setProviders)
        .catch(() => setProviders([]));
    }, []),
  );

  const finish = useCallback(async () => {
    setBusy(true);
    try {
      if (gradeLevel.trim() || goals.trim()) {
        await patchIdentity({
          grade_level: gradeLevel.trim(),
          goals: goals.trim(),
        }).catch(() => {
          // A profile that would not save is not worth stranding someone
          // on the last screen of a setup flow.
        });
      }
      if (user?.id !== undefined) await markOnboarded(user.id);
      router.replace("/(tabs)/today");
    } finally {
      setBusy(false);
    }
  }, [gradeLevel, goals, user, router]);

  const skip = useCallback(async () => {
    if (user?.id !== undefined) await markOnboarded(user.id);
    router.replace("/(tabs)/today");
  }, [user, router]);

  const connected = (providers || []).filter((p) => p.connected);

  return (
    <Screen>
      <View
        style={{
          flexDirection: "row",
          alignItems: "center",
          paddingTop: insets.top + space.sm,
          paddingHorizontal: space.lg,
          paddingBottom: space.md,
          gap: space.sm,
        }}
      >
        {/* Progress as three bars rather than "Step 2 of 3": it shows how
            much is left without the student having to read anything. */}
        {[0, 1, 2].map((i) => (
          <View
            key={i}
            style={{
              flex: 1,
              height: 4,
              borderRadius: radius.pill,
              backgroundColor: i <= step ? colors.accent : colors.bgElevated,
            }}
          />
        ))}
        <Pressable onPress={skip} hitSlop={10} accessibilityRole="button">
          <T variant="sm" tone="muted">
            Skip
          </T>
        </Pressable>
      </View>

      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <ScrollView
          contentContainerStyle={{
            padding: space.lg,
            paddingBottom: insets.bottom + space.xl,
            gap: space.lg,
            flexGrow: 1,
          }}
          keyboardShouldPersistTaps="handled"
        >
          {step === 0 ? (
            <>
              <View style={{ gap: space.md, marginTop: space.lg }}>
                <View
                  style={{
                    width: 56,
                    height: 56,
                    borderRadius: radius.lg,
                    backgroundColor: colors.accent,
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Ionicons name="sparkles" size={28} color={colors.onAccent} />
                </View>
                <T variant="xxl" weight="700">
                  Welcome to IntelliPlan
                </T>
                <T tone="secondary">
                  It pulls everything you owe from every platform your school uses,
                  works out what to do first, and builds the week around it.
                </T>
              </View>

              <View style={{ gap: space.sm }}>
                {[
                  { icon: "sunny-outline", title: "One answer to “what now”", body: "Your work, ranked, with the reasoning shown." },
                  { icon: "timer-outline", title: "A timer that keeps you honest", body: "Focus on one thing; the plan learns how long you actually take." },
                  { icon: "chatbubble-ellipses-outline", title: "Plani explains, never just answers", body: "Photograph a worksheet and it works through it with you." },
                ].map((f) => (
                  <Card key={f.title} style={{ flexDirection: "row", gap: space.md, alignItems: "center" }}>
                    <Ionicons name={f.icon as never} size={22} color={colors.accent} />
                    <View style={{ flex: 1 }}>
                      <T variant="base" weight="600">
                        {f.title}
                      </T>
                      <T variant="sm" tone="muted">
                        {f.body}
                      </T>
                    </View>
                  </Card>
                ))}
              </View>

              <Button title="Get started" icon="arrow-forward" onPress={() => setStep(1)} />
            </>
          ) : step === 1 ? (
            <>
              <View style={{ gap: space.sm, marginTop: space.lg }}>
                <T variant="xl" weight="700">
                  Connect your school
                </T>
                <T tone="secondary">
                  This is what fills the app with your actual assignments and grades.
                  You can do it later — everything else works without it.
                </T>
              </View>

              {connected.length ? (
                <Notice
                  tone="accent"
                  icon="checkmark-circle-outline"
                  text={`Connected: ${connected.map((p) => p.display_name).join(", ")}.`}
                />
              ) : null}

              <View style={{ gap: space.sm }}>
                {(providers || [])
                  .filter((p) => p.configured && !p.connected)
                  .slice(0, 5)
                  .map((p) => (
                    <Pressable
                      key={p.key}
                      accessibilityRole="button"
                      onPress={() =>
                        WebBrowser.openBrowserAsync(
                          `${API_BASE}/integrations?provider=${encodeURIComponent(p.key)}`,
                        ).catch(() => setNote("Couldn't open the browser."))
                      }
                    >
                      <Card style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
                        <Ionicons name="link-outline" size={20} color={colors.textMuted} />
                        <T variant="base" weight="600" style={{ flex: 1 }}>
                          {p.display_name}
                        </T>
                        <Ionicons name="open-outline" size={17} color={colors.accent} />
                      </Card>
                    </Pressable>
                  ))}
                {providers && !providers.some((p) => p.configured) ? (
                  <Notice
                    text="This IntelliPlan server has no school integrations configured, so there's nothing to connect yet."
                    icon="information-circle-outline"
                  />
                ) : null}
              </View>

              {note ? <Notice text={note} icon="alert-circle-outline" /> : null}

              <T variant="xs" tone="muted" style={{ textAlign: "center" }}>
                Connecting opens IntelliPlan in your browser, so your school password
                goes straight to the site and never through this app.
              </T>

              <Button title="Next" icon="arrow-forward" onPress={() => setStep(2)} />
            </>
          ) : (
            <>
              <View style={{ gap: space.sm, marginTop: space.lg }}>
                <T variant="xl" weight="700">
                  A little about you
                </T>
                <T tone="secondary">
                  Plani and the scheduler both read this. Skipping it is fine — you
                  can fill it in later from your profile.
                </T>
              </View>

              <Field
                label="Grade level"
                placeholder="e.g. 11th grade"
                value={gradeLevel}
                onChangeText={setGradeLevel}
              />
              <Field
                label="What are you working toward?"
                placeholder="Keep every course above a B, get the AP credit…"
                value={goals}
                onChangeText={setGoals}
                multiline
              />

              <Card style={{ gap: space.sm }}>
                <Label>Deadline reminders</Label>
                <T variant="sm" tone="secondary">
                  Your phone can nudge you the evening before something is due.
                </T>
                <View style={{ flexDirection: "row", gap: 6, flexWrap: "wrap" }}>
                  <Chip label="Works offline" icon="cloud-offline-outline" />
                  <Chip label="No account needed" icon="phone-portrait-outline" />
                </View>
                <Button
                  title="Turn on reminders"
                  kind="secondary"
                  icon="notifications-outline"
                  onPress={async () => {
                    const ok = await setRemindersEnabled(true);
                    setNote(
                      ok
                        ? "Reminders are on. They'll be scheduled once your work loads."
                        : "Notifications are turned off for IntelliPlan in your phone's settings.",
                    );
                  }}
                />
              </Card>

              {note ? <Notice tone="accent" text={note} icon="information-circle-outline" /> : null}

              <Button title="Finish setup" icon="checkmark" busy={busy} onPress={finish} />
            </>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}
