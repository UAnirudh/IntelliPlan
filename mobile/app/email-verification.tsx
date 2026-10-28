import React, { useCallback, useEffect, useState } from "react";
import { ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { Button, Card, Notice, Screen, T } from "../components/ui";
import { useAuth } from "../lib/auth";
import { resendEmailVerification } from "../lib/api";
import { hasOnboarded } from "../lib/onboarding";
import { useTheme } from "../theme/ThemeProvider";
import { space } from "../theme/tokens";

export default function EmailVerificationScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const { user, refreshUser } = useAuth();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const continueToApp = useCallback(async () => {
    if (!user) {
      router.replace("/login");
      return;
    }
    const onboarded = await hasOnboarded(user.id);
    router.replace(onboarded ? "/(tabs)/today" : "/onboarding");
  }, [router, user]);

  const checkStatus = useCallback(async () => {
    if (!user) {
      router.replace("/login");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const me = await refreshUser();
      if (me.email_verified) await continueToApp();
      else setNote("Your address is not confirmed yet. Open the link in your email, then check again.");
    } catch (e: any) {
      setError(e?.message || "Couldn't check your email status. Try again when you're online.");
    } finally {
      setBusy(false);
    }
  }, [continueToApp, refreshUser, router, user]);

  useEffect(() => {
    if (!user) router.replace("/login");
    else if (user.age_consent_status === "age_required") router.replace("/age-setup");
    else if (user.age_consent_status === "parent_consent_pending") router.replace("/parent-consent-pending");
    else if (user.email_verified) void continueToApp();
    else setNote("If your account needs verification, a secure confirmation link has been sent.");
  }, [continueToApp, router, user]);

  async function resend() {
    setBusy(true);
    setError(null);
    try {
      const result = await resendEmailVerification();
      if (result.email_verified) {
        const me = await refreshUser();
        if (me.email_verified) await continueToApp();
      }
      setNote(result.message);
    } catch (e: any) {
      setError(e?.message || "Couldn't request another email. Try again later.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ flexGrow: 1, justifyContent: "center", padding: space.xl,
        paddingTop: insets.top + space.xl, paddingBottom: insets.bottom + space.xl, gap: space.lg }}>
        <View style={{ width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center",
          backgroundColor: colors.accentSoft }}>
          <Ionicons name="mail-outline" size={25} color={colors.accent} />
        </View>
        <View style={{ gap: space.sm }}>
          <T variant="xxl" weight="700">Confirm your school email</T>
          <T tone="secondary">
            Confirm that you own this address to receive any verified school benefits linked to it.
          </T>
        </View>
        <Card style={{ gap: space.xs }}>
          <T variant="xs" tone="muted">ACCOUNT EMAIL</T>
          <T variant="base" weight="600">{user?.email || "Your account email"}</T>
        </Card>
        {note ? <Notice tone="accent" icon="mail-outline" text={note} /> : null}
        {error ? <Notice tone="warn" icon="alert-circle-outline" text={error} /> : null}
        <Button title="I've confirmed — check status" icon="refresh" kind="secondary" busy={busy} onPress={checkStatus} />
        <Button title="Send another confirmation email" icon="mail-outline" busy={busy} onPress={resend} />
        <Button title="Continue to IntelliPlan" kind="ghost" onPress={continueToApp} />
      </ScrollView>
    </Screen>
  );
}
