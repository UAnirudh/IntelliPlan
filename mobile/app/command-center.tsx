import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Keyboard, KeyboardAvoidingView, Linking, Platform, Pressable, ScrollView, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import * as WebBrowser from "expo-web-browser";
import { askCommandCenter, ChatMessage, CommandCenterReply, getToday, PlannedTask, startLinkSession, TodayPayload } from "../lib/api";
import { dueLabel, minutes } from "../lib/format";
import { useAuth } from "../lib/auth";
import { useQuery } from "../lib/useQuery";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, ErrorState, Label, Loading, Notice, Screen, T } from "../components/ui";
import { Header } from "../components/Header";
import { TutorResponse } from "../components/TutorResponse";

type ChatItem =
  | { id: string; type: "message"; role: "user" | "assistant"; text: string }
  | { id: string; type: "action"; text: string }
  | { id: string; type: "schedule"; schedule: NonNullable<CommandCenterReply["schedule"]> }
  | { id: string; type: "links"; links: NonNullable<CommandCenterReply["links"]> };

const STARTERS = [
  { label: "Plan my week", prompt: "Schedule my week for 2 hours a day after 4:30 PM and put it on my calendar.", icon: "calendar-outline" as const },
  { label: "Add a deadline", prompt: "I have a math test Friday and an English essay due Monday. Add them and make a plan.", icon: "add-circle-outline" as const },
  { label: "What first?", prompt: "What should I focus on first today?", icon: "flash-outline" as const },
];

const INTERNAL_ROUTES: Record<string, string> = {
  "/dashboard": "/(tabs)/today", "/scheduler": "/(tabs)/plan", "/tutor": "/(tabs)/plani",
  "/grades": "/(tabs)/grades", "/gradebook": "/(tabs)/grades", "/streak": "/streak",
  "/pet": "/pet", "/command-center": "/command-center", "/settings": "/settings", "/memories": "/memories",
};

const WEBSITE_PAGES: Record<string, string> = { "/my-courses": "courses", "/foundations": "foundations" };
const CONNECT_PROVIDERS: Record<string, string> = { "/oauth/canvas": "canvas", "/oauth/google": "google", "/oauth/notion": "notion" };

/** Native, chat-first planning with the same task, calendar, and schedule tools as web. */
export default function CommandCenterScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { user } = useAuth();
  const today = useQuery<TodayPayload>("command-center-today", getToday);
  const scrollRef = useRef<ScrollView>(null);
  const inputRef = useRef<TextInput>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [keyboardUp, setKeyboardUp] = useState(false);

  useEffect(() => {
    const show = Keyboard.addListener(Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow", () => setKeyboardUp(true));
    const hide = Keyboard.addListener(Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide", () => setKeyboardUp(false));
    return () => { show.remove(); hide.remove(); };
  }, []);

  const messages = useMemo<ChatMessage[]>(() => items
    .filter((item): item is Extract<ChatItem, { type: "message" }> => item.type === "message")
    .map(({ role, text }) => ({ role, content: text })), [items]);

  const send = useCallback(async (text: string) => {
    const content = text.trim();
    if (!content || busy) return;
    setDraft("");
    setBusy(true);
    setError(null);
    Haptics.selectionAsync().catch(() => {});
    const now = Date.now();
    const history = [...messages, { role: "user" as const, content }];
    setItems((prev) => [...prev, { id: `${now}-user`, type: "message", role: "user", text: content }]);
    requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }));
    try {
      const result = await askCommandCenter(history);
      if (result.error === "account_changed") throw new Error("Your account changed. Please reopen Command Center.");
      const next: ChatItem[] = [];
      (result.actions || []).forEach((action, index) => next.push({ id: `${now}-action-${index}`, type: "action", text: action }));
      next.push({ id: `${now}-assistant`, type: "message", role: "assistant", text: result.reply || result.message || "I couldn't complete that. Try again." });
      if (result.schedule && Array.isArray(result.schedule.schedule)) next.push({ id: `${now}-schedule`, type: "schedule", schedule: result.schedule });
      if (result.links?.length) next.push({ id: `${now}-links`, type: "links", links: result.links });
      setItems((prev) => [...prev, ...next]);
      if (result.refresh) today.reload().catch(() => {});
      if (result.navigate && (INTERNAL_ROUTES[result.navigate] || WEBSITE_PAGES[result.navigate] || CONNECT_PROVIDERS[result.navigate])) {
        setTimeout(() => openSafeLink(result.navigate!, router), 900);
      }
    } catch (e: any) {
      const detail = e?.message === "account_changed"
        ? "Your account changed. Please reopen Command Center."
        : e?.message || "I couldn't reach Plani. Check your connection and try again.";
      setError(detail);
      setItems((prev) => [...prev, { id: `${now}-error`, type: "message", role: "assistant", text: detail }]);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setBusy(false);
      requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }));
    }
  }, [busy, messages, router, today]);

  const tasks = today.data?.plan?.slice(0, 4) || [];
  const firstTask = tasks[0];
  const openTask = useCallback((task: PlannedTask) => {
    router.push({ pathname: "/focus", params: { title: task.title, course: task.course || "", taskId: task.source_ref || "", dueDate: task.due_date || "" } });
  }, [router]);

  return (
    <Screen>
      <Header title="Command Center" subtitle="Plan your day with Plani" showBack showSettings />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView
          ref={scrollRef}
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: space.lg }}
          onContentSizeChange={() => { if (items.length) scrollRef.current?.scrollToEnd({ animated: true }); }}
        >
          {today.error && !today.data ? <ErrorState message={today.error} onRetry={today.reload} /> : null}
          {today.stale ? <Notice text="Offline — showing the last saved plan. Plani needs a connection to make changes." icon="cloud-offline-outline" /> : null}
          {error ? <Notice text={error} icon="alert-circle-outline" /> : null}
          {today.loading && !today.data ? <Loading label="Getting your day ready…" /> : null}

          {firstTask ? <FirstTask task={firstTask} greeting={today.data?.student?.greeting} name={user?.name || today.data?.student?.name} onStart={() => openTask(firstTask)} onWeek={() => router.push("/(tabs)/plan")} /> : (
            !today.loading && !today.error ? <EmptyPlan onAsk={() => inputRef.current?.focus()} onPlan={() => router.push("/(tabs)/plan")} /> : null
          )}

          {tasks.length > 1 ? (
            <View style={{ gap: space.sm }}>
              <View style={{ flexDirection: "row", alignItems: "center" }}>
                <T variant="lg" weight="700" style={{ flex: 1 }}>Today</T>
                <Pressable onPress={() => router.push("/(tabs)/today")} accessibilityRole="button" accessibilityLabel="See your full day"><T tone="accent" weight="600">See all</T></Pressable>
              </View>
              <Card style={{ paddingVertical: 0 }}>{tasks.map((task, index) => <TaskLine key={`${task.id}-${index}`} task={task} last={index === tasks.length - 1} onPress={() => openTask(task)} />)}</Card>
            </View>
          ) : null}

          {!items.length ? (
            <View style={{ gap: space.sm }}>
              <Label>Ask Plani</Label>
              <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm }}>
                {STARTERS.map((starter) => <Pressable key={starter.label} onPress={() => send(starter.prompt)} accessibilityRole="button" style={({ pressed }) => ({ flexDirection: "row", alignItems: "center", gap: space.xs, minHeight: 44, borderWidth: 1, borderColor: colors.border, borderRadius: radius.pill, backgroundColor: pressed ? colors.accentSoft : colors.bgElevated, paddingHorizontal: space.md })}><Ionicons name={starter.icon} size={17} color={colors.accent} /><T variant="sm" weight="600">{starter.label}</T></Pressable>)}
              </View>
            </View>
          ) : null}

          {items.map((item) => <ChatRow key={item.id} item={item} onLink={(url) => openSafeLink(url, router)} />)}
          {busy ? <View accessibilityLabel="Plani is thinking" style={{ alignSelf: "flex-start", padding: space.md, borderRadius: radius.lg, backgroundColor: colors.bgElevated }}><T tone="muted">Plani is thinking…</T></View> : null}
        </ScrollView>

        <View style={{ borderTopWidth: 1, borderTopColor: colors.border, paddingHorizontal: space.lg, paddingTop: space.sm, paddingBottom: insets.bottom + (keyboardUp ? space.sm : space.md), backgroundColor: colors.bg }}>
          <View style={{ flexDirection: "row", alignItems: "flex-end", gap: space.sm, padding: space.sm, borderWidth: 1, borderColor: colors.border, borderRadius: radius.xl, backgroundColor: colors.bgElevated }}>
            <TextInput ref={inputRef} value={draft} onChangeText={setDraft} onSubmitEditing={() => send(draft)} placeholder="Tell Plani what needs to happen…" placeholderTextColor={colors.textMuted} accessibilityLabel="Message Plani" multiline maxLength={4000} editable={!busy} style={{ flex: 1, minHeight: 44, maxHeight: 132, paddingHorizontal: space.sm, paddingTop: space.sm, color: colors.textPrimary, fontSize: 16, lineHeight: 22 }} />
            <Pressable onPress={() => send(draft)} disabled={busy || !draft.trim()} accessibilityRole="button" accessibilityLabel="Send message to Plani" style={{ width: 44, height: 44, borderRadius: radius.pill, backgroundColor: busy || !draft.trim() ? colors.bgSecondary : colors.accent, alignItems: "center", justifyContent: "center" }}><Ionicons name="arrow-up" size={22} color={busy || !draft.trim() ? colors.textMuted : colors.onAccent} /></Pressable>
          </View>
          <T variant="xs" tone="muted" style={{ textAlign: "center", marginTop: space.xs }}>Creates tasks and schedules when you ask. Review changes in your plan.</T>
        </View>
      </KeyboardAvoidingView>
    </Screen>
  );
}

function FirstTask({ task, greeting, name, onStart, onWeek }: { task: PlannedTask; greeting?: string; name?: string | null; onStart: () => void; onWeek: () => void }) {
  const { colors } = useTheme();
  return <Card style={{ gap: space.md, borderColor: colors.borderAccent }}>
    <View style={{ flexDirection: "row", alignItems: "center", gap: space.xs }}><Ionicons name="sparkles" size={16} color={colors.accent} /><Label>{greeting || "Your next best step"}{name ? `, ${name.split(" ")[0]}` : ""}</Label></View>
    <View style={{ gap: space.xs }}><T variant="xl" weight="700">{task.title}</T><T variant="sm" tone="muted">{task.course || "Study task"}{task.why_now ? ` · ${task.why_now}` : ""}</T><T variant="xs" tone="muted">{dueLabel(task.due_date)}{task.est_minutes ? ` · ${minutes(task.est_minutes)}` : ""}</T></View>
    <View style={{ flexDirection: "row", gap: space.sm }}><View style={{ flex: 1 }}><Button title="Start focus" icon="arrow-forward" onPress={onStart} /></View><Button title="My week" icon="calendar-outline" kind="secondary" onPress={onWeek} /></View>
  </Card>;
}

function EmptyPlan({ onAsk, onPlan }: { onAsk: () => void; onPlan: () => void }) {
  return <Card style={{ gap: space.sm }}><T variant="lg" weight="700">What is on your plate?</T><T variant="sm" tone="muted">Add a deadline or ask Plani to make a plan around the time you have.</T><View style={{ flexDirection: "row", gap: space.sm }}><View style={{ flex: 1 }}><Button title="Ask Plani" icon="chatbubble-outline" onPress={onAsk} /></View><Button title="Open plan" kind="secondary" icon="calendar-outline" onPress={onPlan} /></View></Card>;
}

function TaskLine({ task, last, onPress }: { task: PlannedTask; last: boolean; onPress: () => void }) {
  const { colors } = useTheme();
  return <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={`Start ${task.title}`} style={({ pressed }) => ({ minHeight: 72, flexDirection: "row", alignItems: "center", gap: space.md, paddingHorizontal: space.md, borderBottomWidth: last ? 0 : 1, borderBottomColor: colors.border, backgroundColor: pressed ? colors.accentSoft : "transparent" })}>
    <View style={{ width: 38, height: 38, borderRadius: radius.md, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" }}><Ionicons name="book-outline" size={19} color={colors.accent} /></View>
    <View style={{ flex: 1, gap: 3 }}><T variant="sm" weight="700" numberOfLines={1}>{task.title}</T><T variant="xs" tone="muted" numberOfLines={1}>{task.course || "Study task"} · {dueLabel(task.due_date)}</T></View>
    <Ionicons name="chevron-forward" size={19} color={colors.textMuted} />
  </Pressable>;
}

function ChatRow({ item, onLink }: { item: ChatItem; onLink: (url: string) => void }) {
  const { colors } = useTheme();
  if (item.type === "action") return <Card style={{ padding: space.md }}><View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}><Ionicons name="checkmark-circle" size={18} color={colors.ok} /><T variant="sm" style={{ flex: 1 }}>{item.text.replace(/^[✓→]\s*/, "")}</T></View></Card>;
  if (item.type === "schedule") return <ScheduleCard schedule={item.schedule} />;
  if (item.type === "links") return <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm }}>{item.links.filter((link) => safeUrl(link.url)).map((link) => <Button key={`${link.label}-${link.url}`} title={link.label} kind="secondary" icon="open-outline" onPress={() => onLink(link.url)} />)}</View>;
  const user = item.role === "user";
  return <View style={{ alignSelf: user ? "flex-end" : "stretch", maxWidth: "92%", paddingHorizontal: user ? space.md : 0, paddingVertical: user ? space.sm : space.xs, borderRadius: radius.lg, backgroundColor: user ? colors.accentSoft : "transparent" }}>{user ? <T variant="sm" style={{ color: colors.textPrimary, lineHeight: 23 }}>{item.text}</T> : <TutorResponse text={item.text} />}</View>;
}

function ScheduleCard({ schedule }: { schedule: NonNullable<CommandCenterReply["schedule"]> }) {
  const { colors } = useTheme();
  const days = (schedule.schedule || []).slice(0, 7);
  return <Card style={{ gap: space.md }}>
    <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}><Ionicons name="calendar-outline" size={20} color={colors.accent} /><View style={{ flex: 1 }}><Label>{schedule.preview ? "Plan preview · not saved" : "Study plan saved"}</Label><T variant="base" weight="700">{schedule.total_study_time || "Your study plan"}</T></View></View>
    {days.map((day, index) => {
      const blocks = (day.blocks || []).filter((block) => !block.is_break).slice(0, 3);
      return <View key={day.date || day.day_name || index} style={{ gap: space.xs, paddingTop: space.sm, borderTopWidth: 1, borderTopColor: colors.border }}><T variant="sm" weight="700">{day.day_name || day.date || `Day ${index + 1}`}</T>{blocks.length ? blocks.map((block, i) => <T key={i} variant="xs" tone="muted">{block.time_slot ? `${block.time_slot.split(/\s*[-–]\s*/)[0]} · ` : ""}{block.assignment || block.title || "Study block"}</T>) : <T variant="xs" tone="muted">No study blocks</T>}</View>;
    })}
  </Card>;
}

function safeUrl(url: string) { return /^\/[a-z0-9\-/#?=&]*$/i.test(url) || /^https:\/\//i.test(url); }

async function openSafeLink(url: string, router: ReturnType<typeof useRouter>) {
  try {
    const path = url.split(/[?#]/)[0];
    const route = INTERNAL_ROUTES[path];
    if (route) { router.push(route as never); return; }
    const page = WEBSITE_PAGES[path];
    const provider = CONNECT_PROVIDERS[path];
    if (page || provider) {
      const session = await startLinkSession(page || provider!);
      await WebBrowser.openBrowserAsync(session.url, { presentationStyle: WebBrowser.WebBrowserPresentationStyle.PAGE_SHEET });
      return;
    }
    if (/^https:\/\//i.test(url)) await Linking.openURL(url);
  } catch {
    // The conversation remains usable if a link handoff fails.
  }
}
