import React, { useCallback, useState } from "react";
import { Linking, Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as WebBrowser from "expo-web-browser";
import {
  activateGoogleAccount,
  disconnectGoogleCalendar,
  getGoogleCalendarStatus,
  getSchoolProfiles,
  GoogleCalendarStatus,
  removeGoogleAccount,
  removeSchoolProfile,
  renameSchoolProfile,
  SchoolProfiles,
  startLinkSession,
  switchSchoolProfile,
} from "../lib/api";
import { clearQueryCache } from "../lib/useQuery";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, Chip, Field, Label, Loading, Notice, Screen, T } from "../components/ui";
import { useConfirm } from "../components/Confirm";

/** How each linked platform calls itself, for the row subtitle. */
const PLATFORM_LABEL: Record<string, string> = {
  canvas: "Canvas",
  studentvue: "StudentVue",
  schoology: "Schoology",
  classroom: "Google Classroom",
  blackboard: "Blackboard",
};

/** Native-friendly account connections. OAuth stays in a real browser, where
 * Google and Canvas can enforce their security policies safely.
 *
 * The browser is opened *already signed in*. Both providers' flows begin at
 * a Flask route guarded by `current_user` — they read the session cookie,
 * not the bearer token this app holds — so a plain `openBrowserAsync` lands
 * on the login page and the connection attaches to nobody. Instead the app
 * mints a one-time hand-off code (`POST /api/v1/link/session`) and opens
 * `/link/<code>`, which signs that browser in and forwards to the provider.
 *
 * `openAuthSessionAsync` rather than `openBrowserAsync` for the same reason
 * it matters elsewhere: it watches for the `intelliplan://connected`
 * redirect and closes the browser itself, instead of leaving the student on
 * the website with no way back. */
export default function AccountsScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const confirm = useConfirm();
  const [status, setStatus] = useState<GoogleCalendarStatus | null>(null);
  const [profiles, setProfiles] = useState<SchoolProfiles | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  /** Which profile row is mid-request, so one row spins rather than all. */
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  /** The profile being renamed inline, and the text so far. */
  const [editing, setEditing] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    // Two independent calls, and one failing says nothing about the other —
    // a student with no Google connection should still see their school
    // profiles, so these are settled rather than raced.
    const [gcal, profs] = await Promise.allSettled([
      getGoogleCalendarStatus(),
      getSchoolProfiles(),
    ]);
    if (gcal.status === "fulfilled") setStatus(gcal.value);
    if (profs.status === "fulfilled") setProfiles(profs.value);
    if (gcal.status === "rejected" && profs.status === "rejected") {
      setNote((gcal.reason as any)?.message || "Couldn't check connected accounts.");
    }
    setLoading(false);
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  async function connect(provider: "google" | "canvas", label: string) {
    setNote(null);
    setBusy(true);
    try {
      const session = await startLinkSession(provider);
      try {
        const result = await WebBrowser.openAuthSessionAsync(session.url, session.return_url);
        if (result.type === "success") setNote(`${label} connected.`);
      } catch {
        // Some Android builds ship no custom-tab handler. The hand-off URL
        // still works in any browser; it just cannot close itself, so the
        // student comes back with the system gesture and the refresh below
        // picks the connection up.
        const supported = await Linking.canOpenURL(session.url);
        if (supported) await Linking.openURL(session.url);
        else setNote("This device could not open a browser.");
      }
      await load();
    } catch (e: any) {
      setNote(e?.message || `Couldn't start connecting ${label}.`);
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    // This endpoint drops every GoogleIntegration row, not just the active
    // one, so with several connected the wording has to say so — otherwise
    // someone tidying up one account loses all of them.
    const many = (status?.accounts || []).length > 1;
    const choice = await confirm({
      title: many ? "Disconnect all Google accounts?" : "Disconnect Google Calendar?",
      message: many
        ? `All ${status!.accounts.length} connected Google accounts will be removed. Your IntelliPlan tasks stay safe, but calendar availability will no longer be used.`
        : "Your IntelliPlan tasks stay safe, but calendar availability will no longer be used.",
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

  /**
   * Make a different school account the one the app is about.
   *
   * The cached responses have to go with it. Every list in the app —
   * today, tasks, grades — is keyed by endpoint and not by profile, so
   * keeping them would show the previous school's assignments under the
   * newly selected profile's name until each screen happened to refetch.
   */
  async function switchTo(id: string, name: string) {
    setNote(null);
    setRowBusy(id);
    try {
      await switchSchoolProfile(id);
      await clearQueryCache().catch(() => {});
      await load();
      setNote(`Now showing ${name}.`);
    } catch (e: any) {
      setNote(e?.message || "Couldn't switch profile.");
    } finally {
      setRowBusy(null);
    }
  }

  async function saveRename(id: string) {
    const name = draftName.trim();
    if (!name) {
      setNote("Give the profile a name.");
      return;
    }
    setRowBusy(id);
    try {
      await renameSchoolProfile(id, name);
      setEditing(null);
      await load();
    } catch (e: any) {
      setNote(e?.message || "Couldn't rename that profile.");
    } finally {
      setRowBusy(null);
    }
  }

  async function removeProfile(p: { id: string; name: string; is_active: boolean }) {
    const choice = await confirm({
      title: `Remove ${p.name}?`,
      message: p.is_active
        ? "This is the profile you're using now. Its assignments will disappear from the app until you connect it again."
        : "Its assignments will disappear from the app until you connect it again.",
      actions: [{ label: "Remove", destructive: true }, { label: "Cancel", cancel: true }],
    });
    if (choice !== 0) return;
    setRowBusy(p.id);
    try {
      await removeSchoolProfile(p.id);
      await clearQueryCache().catch(() => {});
      await load();
    } catch (e: any) {
      setNote(e?.message || "Couldn't remove that profile.");
    } finally {
      setRowBusy(null);
    }
  }

  async function useGoogleAccount(id: number) {
    setRowBusy(`g${id}`);
    try {
      await activateGoogleAccount(id);
      await load();
    } catch (e: any) {
      setNote(e?.message || "Couldn't switch Google account.");
    } finally {
      setRowBusy(null);
    }
  }

  async function dropGoogleAccount(id: number, email?: string) {
    const choice = await confirm({
      title: "Remove this Google account?",
      message: `${email || "This account"} will no longer be used for calendar availability.`,
      actions: [{ label: "Remove", destructive: true }, { label: "Cancel", cancel: true }],
    });
    if (choice !== 0) return;
    setRowBusy(`g${id}`);
    try {
      await removeGoogleAccount(id);
      await load();
    } catch (e: any) {
      setNote(e?.message || "Couldn't remove that Google account.");
    } finally {
      setRowBusy(null);
    }
  }

  const schoolProfiles = profiles?.profiles || [];

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

          {/* First, because it is the one setting on this screen that
              changes what every other screen is showing. */}
          {schoolProfiles.length ? (
            <Card style={{ gap: space.md }}>
              <View style={{ gap: 2 }}>
                <Label>School profiles</Label>
                <T variant="sm" tone="muted">
                  {schoolProfiles.length > 1
                    ? "The active one decides whose assignments and grades the app shows."
                    : "The account your assignments and grades come from."}
                </T>
              </View>

              {schoolProfiles.map((p) => {
                const spinning = rowBusy === p.id;
                if (editing === p.id) {
                  return (
                    <View key={p.id} style={{ gap: space.sm }}>
                      <Field
                        label="Profile name"
                        value={draftName}
                        onChangeText={setDraftName}
                        autoFocus
                        returnKeyType="done"
                        onSubmitEditing={() => saveRename(p.id)}
                      />
                      <View style={{ flexDirection: "row", gap: space.sm }}>
                        <View style={{ flex: 1 }}>
                          <Button title="Save" busy={spinning} onPress={() => saveRename(p.id)} />
                        </View>
                        <View style={{ flex: 1 }}>
                          <Button title="Cancel" kind="secondary" onPress={() => setEditing(null)} />
                        </View>
                      </View>
                    </View>
                  );
                }
                return (
                  <Pressable
                    key={p.id}
                    disabled={p.is_active || !!rowBusy}
                    onPress={() => switchTo(p.id, p.name)}
                    accessibilityRole="button"
                    accessibilityState={{ selected: p.is_active, disabled: p.is_active }}
                    accessibilityLabel={
                      p.is_active ? `${p.name}, active profile` : `Switch to ${p.name}`
                    }
                    style={{
                      flexDirection: "row",
                      alignItems: "center",
                      gap: space.md,
                      padding: space.md,
                      borderRadius: radius.md,
                      borderWidth: 1,
                      borderColor: p.is_active ? colors.borderAccent : colors.border,
                      backgroundColor: p.is_active ? colors.accentSoft : "transparent",
                      opacity: spinning ? 0.5 : 1,
                    }}
                  >
                    <Ionicons
                      name={p.is_active ? "radio-button-on" : "radio-button-off"}
                      size={20}
                      color={p.is_active ? colors.accent : colors.textSecondary}
                    />
                    <View style={{ flex: 1 }}>
                      <T variant="base" weight="600">{p.name}</T>
                      <T variant="xs" tone="muted">
                        {PLATFORM_LABEL[(p.login_type || "").toLowerCase()] || p.login_type}
                      </T>
                    </View>
                    <Pressable
                      hitSlop={10}
                      disabled={!!rowBusy}
                      onPress={() => { setEditing(p.id); setDraftName(p.name); }}
                      accessibilityRole="button"
                      accessibilityLabel={`Rename ${p.name}`}
                    >
                      <Ionicons name="create-outline" size={20} color={colors.textSecondary} />
                    </Pressable>
                    <Pressable
                      hitSlop={10}
                      disabled={!!rowBusy}
                      onPress={() => removeProfile(p)}
                      accessibilityRole="button"
                      accessibilityLabel={`Remove ${p.name}`}
                    >
                      <Ionicons name="trash-outline" size={20} color={colors.danger} />
                    </Pressable>
                  </Pressable>
                );
              })}

              <Button
                title="Connect another school"
                kind="secondary"
                icon="add"
                onPress={() => router.push("/connect")}
              />
            </Card>
          ) : null}

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
                {/* More than one Google account is normal — a school one
                    and a personal one — and only the active one is read
                    for availability. Listing just the active email hid
                    both which others existed and how to pick between
                    them. */}
                {(status.accounts || []).length > 1 ? (
                  (status.accounts || []).map((a) => {
                    const active = a.is_active || a.id === status.active_id;
                    return (
                      <Pressable
                        key={a.id}
                        disabled={active || !!rowBusy}
                        onPress={() => useGoogleAccount(a.id)}
                        accessibilityRole="button"
                        accessibilityState={{ selected: active, disabled: active }}
                        accessibilityLabel={
                          active
                            ? `${a.email || "Google account"}, in use`
                            : `Use ${a.email || "this Google account"}`
                        }
                        style={{
                          flexDirection: "row",
                          alignItems: "center",
                          gap: space.md,
                          padding: space.md,
                          borderRadius: radius.md,
                          borderWidth: 1,
                          borderColor: active ? colors.borderAccent : colors.border,
                          backgroundColor: active ? colors.accentSoft : "transparent",
                          opacity: rowBusy === `g${a.id}` ? 0.5 : 1,
                        }}
                      >
                        <Ionicons
                          name={active ? "radio-button-on" : "radio-button-off"}
                          size={20}
                          color={active ? colors.accent : colors.textSecondary}
                        />
                        <T variant="sm" style={{ flex: 1 }} numberOfLines={1}>
                          {a.email || a.name || `Account ${a.id}`}
                        </T>
                        <Pressable
                          hitSlop={10}
                          disabled={!!rowBusy}
                          onPress={() => dropGoogleAccount(a.id, a.email)}
                          accessibilityRole="button"
                          accessibilityLabel={`Remove ${a.email || "this Google account"}`}
                        >
                          <Ionicons name="trash-outline" size={20} color={colors.danger} />
                        </Pressable>
                      </Pressable>
                    );
                  })
                ) : (
                  <T variant="sm" tone="secondary">
                    {status.active_email || status.accounts?.[0]?.email || "Google account connected"}
                  </T>
                )}
                <Button
                  title="Add another Google account"
                  kind="secondary"
                  icon="add"
                  busy={busy}
                  onPress={() => connect("google", "Google Calendar")}
                />
                <Button
                  title={
                    (status.accounts || []).length > 1 ? "Disconnect all" : "Disconnect"
                  }
                  kind="danger"
                  busy={busy}
                  onPress={disconnect}
                />
              </>
            ) : (
              <Button
                title="Connect Google Calendar"
                icon="open-outline"
                busy={busy}
                onPress={() => connect("google", "Google Calendar")}
              />
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
            <Button
              title="Connect Canvas"
              icon="open-outline"
              busy={busy}
              onPress={() => connect("canvas", "Canvas")}
            />
          </Card>

          <Notice tone="accent" icon="shield-checkmark-outline" text="Connections open in a real browser so Google and Canvas credentials never pass through the app UI." />
          <T variant="xs" tone="muted" style={{ textAlign: "center" }}>After finishing in the browser, return here and pull down to refresh.</T>
        </ScrollView>
      )}
    </Screen>
  );
}
