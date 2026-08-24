import React, { useCallback, useState } from "react";
import { Linking, Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as WebBrowser from "expo-web-browser";
import { disconnectGoogleCalendar, getGoogleCalendarStatus, GoogleCalendarStatus } from "../lib/api";
import { API_BASE } from "../lib/config";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, Chip, ErrorState, Loading, Notice, Screen, T } from "../components/ui";
import { useConfirm } from "../components/Confirm";

/** Native-friendly account connections. OAuth stays in a real browser, where
 * Google and Canvas can enforce their security policies safely. */
export default function AccountsScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const confirm = useConfirm();
  const [status, setStatus] = useState<GoogleCalendarStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await getGoogleCalendarStatus());
    } catch (e: any) {
      setNote(e?.message || "Couldn't check connected accounts.");
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  async function open(url: string) {
    setNote(null);
    try {
      await WebBrowser.openBrowserAsync(url);
      await load();
    } catch {
      // Some Android builds do not ship a custom-tab handler; fall back to
      // the system URL opener so the student still has a path forward.
      const supported = await Linking.canOpenURL(url);
      if (supported) await Linking.openURL(url);
      else setNote("This device could not open a browser.");
    }
  }

  async function disconnect() {
    const choice = await confirm({
      title: "Disconnect Google Calendar?",
      message: "Your IntelliPlan tasks stay safe, but calendar availability will no longer be used.",
      actions: [{ label: "Disconnect", destructive: true }, { label: "Cancel", cancel: true }],
    });
    if (choice !== 0) return;
    setBusy(true);
    try {
      await disconnectGoogleCalendar();
      await load();
    } catch (e: any) {
      setNote(e?.message || "Couldn't disconnect Google Calendar.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <View style={{ flexDirection: "row", alignItems: "center", paddingTop: insets.top + space.sm, paddingHorizontal: space.lg, paddingBottom: space.md, borderBottomWidth: 1, borderBottomColor: colors.border }}>
        <View style={{ flex: 1 }}>
          <T variant="lg" weight="700">Accounts &amp; calendar</T>
          <T variant="sm" tone="muted">Connect once, plan around real commitments.</T>
        </View>
        <Pressable onPress={() => router.back()} hitSlop={10} accessibilityRole="button" accessibilityLabel="Close">
          <Ionicons name="close" size={26} color={colors.textSecondary} />
        </Pressable>
      </View>

      {loading && !status ? <Loading label="Checking connections…" /> : (
        <ScrollView
          contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: insets.bottom + 40 }}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={colors.accent} />}
        >
          {note ? <Notice text={note} icon="information-circle-outline" /> : null}
          <Card style={{ gap: space.md }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
              <View style={{ width: 42, height: 42, borderRadius: radius.md, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" }}>
                <Ionicons name="logo-google" size={21} color={colors.accent} />
              </View>
              <View style={{ flex: 1 }}>
                <T variant="base" weight="700">Google Calendar</T>
                <T variant="sm" tone="muted">Use real events when IntelliPlan schedules study time.</T>
              </View>
              {status?.connected ? <Chip label="Connected" icon="checkmark-circle" fg={colors.ok} bg={colors.okSoft} /> : null}
            </View>
            {status?.connected ? (
              <>
                <T variant="sm" tone="secondary">{status.active_email || status.accounts?.[0]?.email || "Google account connected"}</T>
                <Button title="Disconnect" kind="danger" busy={busy} onPress={disconnect} />
              </>
            ) : (
              <Button title="Connect Google Calendar" icon="open-outline" onPress={() => open(`${API_BASE}/oauth/google?return=settings`)} />
            )}
          </Card>

          <Card style={{ gap: space.md }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
              <View style={{ width: 42, height: 42, borderRadius: radius.md, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" }}>
                <Ionicons name="school-outline" size={21} color={colors.accent} />
              </View>
              <View style={{ flex: 1 }}>
                <T variant="base" weight="700">Canvas LMS</T>
                <T variant="sm" tone="muted">Open the secure Canvas connection page without typing a long URL.</T>
              </View>
            </View>
            <Button title="Connect Canvas" icon="open-outline" onPress={() => open(`${API_BASE}/login/canvas`)} />
          </Card>

          <Notice tone="accent" icon="shield-checkmark-outline" text="Connections open in a real browser so Google and Canvas credentials never pass through the app UI." />
          <T variant="xs" tone="muted" style={{ textAlign: "center" }}>After finishing in the browser, return here and pull down to refresh.</T>
        </ScrollView>
      )}
    </Screen>
  );
}
