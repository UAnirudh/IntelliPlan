import React, { useEffect, useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Button, Card, Field, Notice, Screen, T } from "../components/ui";
import { useAuth } from "../lib/auth";
import { hasOnboarded } from "../lib/onboarding";
import { submitAge } from "../lib/api";
import { useTheme } from "../theme/ThemeProvider";
import { space } from "../theme/tokens";

export default function AgeSetupScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const { user, refreshUser } = useAuth();
  const [year, setYear] = useState("");
  const [parentEmail, setParentEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function routeIfAlreadyReady() {
      if (!user) {
        router.replace("/login");
        return;
      }
      if (user.age_consent_status === "parent_consent_pending") {
        router.replace("/parent-consent-pending");
        return;
      }
      if (user.age_consent_status === "ready") {
        const onboarded = await hasOnboarded(user.id);
        if (!cancelled) router.replace(onboarded ? "/(tabs)/today" : "/onboarding");
      }
    }
    void routeIfAlreadyReady();
    return () => { cancelled = true; };
  }, [user, router]);

  const yearNumber = /^\d{4}$/.test(year) ? Number(year) : null;
  // Match the server's conservative birth-year-only rule. The server remains
  // authoritative and verifies the year again before recording anything.
  const under13 = yearNumber !== null && new Date().getUTCFullYear() - yearNumber - 1 < 13;

  async function save() {
    setError(null);
    if (yearNumber === null) {
      setError("Enter your four-digit birth year.");
      return;
    }
    if (under13 && !parentEmail.trim()) {
      setError("A parent or guardian's email is required for this age group.");
      return;
    }
    setBusy(true);
    try {
      const result = await submitAge(yearNumber, under13 ? parentEmail.trim() : undefined);
      const me = await refreshUser();
      if (result.age_consent_status === "parent_consent_pending" ||
          me.age_consent_status === "parent_consent_pending") {
        router.replace("/parent-consent-pending");
        return;
      }
      const onboarded = user ? await hasOnboarded(user.id) : false;
      router.replace(onboarded ? "/(tabs)/today" : "/onboarding");
    } catch (e: any) {
      setError(e?.message || "Couldn't save age setup. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView
          contentContainerStyle={{ flexGrow: 1, justifyContent: "center", padding: space.lg,
            paddingTop: insets.top + space.lg, paddingBottom: insets.bottom + space.lg, gap: space.lg }}
          keyboardShouldPersistTaps="handled"
        >
          <View style={{ gap: space.sm }}>
            <T variant="xs" tone="accent" weight="700">ACCOUNT SETUP</T>
            <T variant="xxl" weight="700">Your age helps protect your account</T>
            <T tone="secondary">
              Enter your birth year to set the right privacy and consent protections. We store your birth year, not a full birth date.
            </T>
          </View>

          <Card style={{ gap: space.md }}>
            <Field
              label="Birth year"
              placeholder="e.g. 2010"
              value={year}
              onChangeText={(value) => setYear(value.replace(/\D/g, "").slice(0, 4))}
              keyboardType="number-pad"
              autoComplete="off"
              maxLength={4}
              accessibilityLabel="Birth year"
            />
            {under13 ? (
              <>
                <Notice
                  tone="accent"
                  icon="shield-checkmark-outline"
                  text="A parent or guardian must approve this account before it can be used."
                />
                <Field
                  label="Parent or guardian email"
                  placeholder="parent@example.com"
                  value={parentEmail}
                  onChangeText={setParentEmail}
                  keyboardType="email-address"
                  autoCapitalize="none"
                  autoCorrect={false}
                  autoComplete="email"
                  textContentType="emailAddress"
                  accessibilityLabel="Parent or guardian email"
                />
                <T variant="xs" tone="muted">
                  It must be a different address from your own. We will send them the existing one-time consent request.
                </T>
              </>
            ) : null}
          </Card>

          {error ? <Notice tone="warn" icon="alert-circle-outline" text={error} /> : null}

          <Button title="Continue" icon="arrow-forward" busy={busy} onPress={save} />
          <T variant="xs" tone="muted" style={{ color: colors.textMuted, textAlign: "center" }}>
            You can continue once this step is complete.
          </T>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}
