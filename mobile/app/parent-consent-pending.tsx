import React, { useCallback, useEffect, useState } from "react";
import { ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Button, Notice, Screen, T } from "../components/ui";
import { useAuth } from "../lib/auth";
import { hasOnboarded } from "../lib/onboarding";
import { resendParentConsent } from "../lib/api";
import { useTheme } from "../theme/ThemeProvider";
import { space } from "../theme/tokens";

export default function ParentConsentPendingScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const { user, refreshUser } = useAuth();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const routeReady = useCallback(async (emailVerified: boolean) => {
    if (!user) return;
    if (!emailVerified) {
      router.replace("/email-verification");
      return;
    }
    const onboarded = await hasOnboarded(user.id);
    router.replace(onboarded ? "/(tabs)/today" : "/onboarding");
  }, [router, user]);

  const checkStatus = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const me = await refreshUser();
      if (me.age_consent_status === "age_required") router.replace("/age-setup");
      else if (me.age_consent_status === "ready") await routeReady(me.email_verified === true);
      else setNote("Approval is still pending. This screen will keep your account protected until it is confirmed.");
    } catch (e: any) {
      setError(e?.message || "Couldn't check approval right now. Try again when you're back online.");
    } finally {
      setBusy(false);
    }
  }, [refreshUser, routeReady, router]);

  useEffect(() => {
    if (!user) router.replace("/login");
    else if (user.age_consent_status === "age_required") router.replace("/age-setup");
    else if (user.age_consent_status === "ready") void routeReady(user.email_verified === true);
    else setNote("We sent a one-time approval request to the parent or guardian address you entered.");
  }, [user, routeReady, router]);

  async function resend() {
    setBusy(true);
    setError(null);
    try {
      const result = await resendParentConsent();
      setNote(result.message);
    } catch (e: any) {
      setError(e?.message || "Couldn't resend the consent request. Try again later.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <ScrollView contentContainerStyle={{ flexGrow: 1, justifyContent: "center", padding: space.xl,
        paddingTop: insets.top + space.xl, paddingBottom: insets.bottom + space.xl, gap: space.lg }}>
        <View style={{ width: 54, height: 54, borderRadius: 27, alignItems: "center", justifyContent: "center",
          backgroundColor: colors.accentSoft }}>
          <T variant="xl" weight="700" tone="accent">✓</T>
        </View>
        <View style={{ gap: space.sm }}>
          <T variant="xxl" weight="700">Waiting for parent approval</T>
          <T tone="secondary">
            IntelliPlan stays locked until your parent or guardian reviews and approves the request. The email link expires after 7 days; resend the request here if it expires.
          </T>
        </View>
        {note ? <Notice tone="accent" icon="mail-outline" text={note} /> : null}
        {error ? <Notice tone="warn" icon="alert-circle-outline" text={error} /> : null}
        <Button title="Check approval" icon="refresh" kind="secondary" busy={busy} onPress={checkStatus} />
        <Button title="Resend parent request" icon="mail-outline" busy={busy} onPress={resend} />
      </ScrollView>
    </Screen>
  );
}
