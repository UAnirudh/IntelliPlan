import React, { useCallback, useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as WebBrowser from "expo-web-browser";
import {
  disconnectProvider,
  getProviders,
  Provider,
  startLinkSession,
  syncProvider,
} from "../lib/api";
import { useQuery } from "../lib/useQuery";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, Chip, EmptyState, ErrorState, Label, Loading, Notice, Screen, T } from "../components/ui";
import { useConfirm } from "../components/Confirm";

/**
 * Connecting a school platform, from the phone.
 *
 * The connect step opens the website rather than reimplementing each
 * provider's flow here. Two reasons, and neither is laziness: the OAuth
 * providers require a real browser redirect to be secure at all — Google
 * refuses to run its flow in an embedded web view, hardest of all against
 * the supervised accounts these students often have — and the credential
 * providers already have a hardened, tested form on the site. A second
 * implementation in a mobile client would be a second place for school
 * credentials to leak.
 *
 * What is new is that the browser no longer opens logged out. The app
 * mints a one-time hand-off code (`POST /api/v1/link/session`), opens the
 * browser at a URL carrying it, and the server signs that browser in and
 * forwards straight to the provider. When the flow finishes the site
 * redirects to `intelliplan://connected`, which closes the browser and
 * brings the student back here with the list already refreshing. One tap,
 * no copy-pasting a token, no "now go and log in again".
 *
 * Sync and disconnect are plain JSON calls, so the things a student does
 * repeatedly never leave the app at all.
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

  const connect = useCallback(
    async (p: Provider) => {
      setNote(null);
      setBusy(p.key);
      try {
        const session = await startLinkSession(p.key);

        // openAuthSessionAsync, not openBrowserAsync: it watches for the
        // return URL and closes the browser itself. With the plain opener
        // the student lands back on the website with no way out but the
        // system back gesture, which on iOS does not exist.
        const result = await WebBrowser.openAuthSessionAsync(session.url, session.return_url);

        if (result.type === "success") {
          setNote(`${p.display_name} connected. Pulling your work in…`);
          await q.refresh();
          // The first sync is what actually fills the app, and a student
          // who just connected should not have to find a second button to
          // make anything appear.
          await syncProvider(p.key).catch(() => {});
          await q.refresh();
        } else {
          // Dismissed or cancelled. The connection may still have gone
          // through — the student may simply have closed the tab after
          // the redirect — so ask the server rather than assuming.
          await q.refresh();
        }
      } catch (e: any) {
        setNote(e?.message || `Couldn't start connecting ${p.display_name}.`);
      } finally {
        setBusy(null);
      }
    },
    [q],
  );

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
            {connected.length ? `${connected.length} connected` : "Nothing connected yet"}
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
            Connecting opens IntelliPlan in your browser, already signed in, so
            your school password goes straight to the site and never through
            this app. You'll come back here automatically.
          </T>
        </ScrollView>
      )}
    </Screen>
  );
}
