import React, { useCallback, useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as WebBrowser from "expo-web-browser";
import { disconnectProvider, getProviders, Provider, syncProvider } from "../lib/api";
import { API_BASE } from "../lib/config";
import { useQuery } from "../lib/useQuery";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, Chip, EmptyState, ErrorState, Label, Loading, Notice, Screen, T } from "../components/ui";
import { useConfirm } from "../components/Confirm";

/**
 * Connecting a school platform, from the phone.
 *
 * The connect step opens the website in an in-app browser rather than
 * reimplementing each provider's flow here. Two reasons, and neither is
 * laziness: the OAuth providers require a real browser redirect to be
 * secure at all, and the credential providers (Canvas tokens, StudentVue
 * passwords) already have a hardened, tested form on the site — a second
 * implementation in a mobile client would be a second place for school
 * credentials to leak.
 *
 * Sync and disconnect are plain JSON calls, so the things a student does
 * repeatedly stay in the app.
 */
export default function ConnectScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const confirm = useConfirm();

  const q = useQuery<Provider[]>("providers", getProviders);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // Coming back from the browser is the moment a connection may have
  // appeared, and it is the only signal we get — the browser tab does not
  // tell us how it went.
  const first = React.useRef(true);
  useFocusEffect(
    useCallback(() => {
      if (first.current) {
        first.current = false;
        return;
      }
      q.refresh();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []),
  );

  const connect = useCallback(async (p: Provider) => {
    setNote(null);
    try {
      await WebBrowser.openBrowserAsync(`${API_BASE}/integrations?provider=${encodeURIComponent(p.key)}`);
    } catch {
      setNote(`Couldn't open the browser to connect ${p.display_name}.`);
    }
  }, []);

  const sync = useCallback(
    async (p: Provider) => {
      setBusy(p.key);
      setNote(null);
      try {
        const res = await syncProvider(p.key);
        setNote(`${p.display_name}: pulled ${res.synced} assignment${res.synced === 1 ? "" : "s"}.`);
        await q.refresh();
      } catch (e: any) {
        setNote(e?.message || `Couldn't sync ${p.display_name}.`);
      } finally {
        setBusy(null);
      }
    },
    [q],
  );

  const disconnect = useCallback(
    async (p: Provider) => {
      const choice = await confirm({
        title: `Disconnect ${p.display_name}?`,
        message: "Assignments already pulled in stay. Nothing new will sync until you reconnect.",
        actions: [
          { label: "Disconnect", destructive: true },
          { label: "Cancel", cancel: true },
        ],
      });
      if (choice !== 0) return;
      setBusy(p.key);
      try {
        await disconnectProvider(p.key);
        await q.refresh();
      } catch (e: any) {
        setNote(e?.message || "That didn't work.");
      } finally {
        setBusy(null);
      }
    },
    [q, confirm],
  );

  const providers = q.data || [];
  const connected = providers.filter((p) => p.connected);

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
        <View style={{ flex: 1 }}>
          <T variant="lg" weight="700">
            Your school
          </T>
          <T variant="sm" tone="muted">
            {connected.length
              ? `${connected.length} connected`
              : "Nothing connected yet"}
          </T>
        </View>
        <Pressable onPress={() => router.back()} hitSlop={10} accessibilityRole="button" accessibilityLabel="Close">
          <Ionicons name="close" size={26} color={colors.textSecondary} />
        </Pressable>
      </View>

      {q.loading ? (
        <Loading label="Checking what's connected…" />
      ) : q.error && !providers.length ? (
        <ErrorState message={q.error} onRetry={q.reload} />
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: space.lg, gap: space.sm, paddingBottom: insets.bottom + 40 }}
          refreshControl={
            <RefreshControl refreshing={q.refreshing} onRefresh={q.refresh} tintColor={colors.accent} />
          }
        >
          {note ? <Notice text={note} tone="accent" icon="information-circle-outline" /> : null}

          {providers.length ? (
            providers.map((p) => (
              <Card key={p.key} style={{ gap: space.md }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
                  <View
                    style={{
                      width: 40,
                      height: 40,
                      borderRadius: radius.md,
                      backgroundColor: p.connected ? colors.accentSoft : colors.bgElevated,
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <Ionicons
                      name={p.connected ? "link" : "link-outline"}
                      size={19}
                      color={p.connected ? colors.accent : colors.textMuted}
                    />
                  </View>
                  <View style={{ flex: 1, gap: 4 }}>
                    <T variant="base" weight="600">
                      {p.display_name}
                    </T>
                    <View style={{ flexDirection: "row", gap: 6, flexWrap: "wrap" }}>
                      {p.connected ? (
                        <Chip label="Connected" icon="checkmark-circle" fg={colors.ok} bg={colors.okSoft} />
                      ) : p.configured ? (
                        <Chip label="Available" />
                      ) : (
                        // A provider whose keys this deployment doesn't hold
                        // cannot be connected however hard anyone taps, so
                        // it says so rather than failing at the browser.
                        <Chip label="Not set up on this server" fg={colors.warnText} bg={colors.warnSoft} />
                      )}
                      {p.last_sync_count ? <Chip label={`${p.last_sync_count} items`} /> : null}
                    </View>
                  </View>
                </View>

                {p.connected ? (
                  <View style={{ flexDirection: "row", gap: space.sm }}>
                    <Button
                      title="Sync now"
                      kind="secondary"
                      icon="refresh"
                      busy={busy === p.key}
                      onPress={() => sync(p)}
                      style={{ flex: 1 }}
                    />
                    <Button
                      title="Disconnect"
                      kind="danger"
                      busy={busy === p.key}
                      onPress={() => disconnect(p)}
                      style={{ flex: 1 }}
                    />
                  </View>
                ) : (
                  <Button
                    title={`Connect ${p.display_name}`}
                    icon="open-outline"
                    disabled={!p.configured}
                    onPress={() => connect(p)}
                  />
                )}
              </Card>
            ))
          ) : (
            <EmptyState
              icon="link-outline"
              title="No platforms available"
              body="This IntelliPlan server doesn't have any school integrations configured."
            />
          )}

          <T variant="xs" tone="muted" style={{ textAlign: "center", marginTop: space.md }}>
            Connecting opens IntelliPlan in your browser so your school credentials
            go straight to the site, never through this app.
          </T>
        </ScrollView>
      )}
    </Screen>
  );
}
