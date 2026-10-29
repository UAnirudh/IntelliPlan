import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Keyboard,
  Modal,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import * as ImagePicker from "expo-image-picker";
import {
  askPlani,
  askPlaniVision,
  ChatMessage,
  Conversation,
  deleteConversation,
  getConversation,
  getConversations,
} from "../../lib/api";
import { useTheme } from "../../theme/ThemeProvider";
import { radius, space } from "../../theme/tokens";
import { Card, Chip, EmptyState, ErrorState, Label, Loading, Notice, Screen, T } from "../../components/ui";
import { useConfirm } from "../../components/Confirm";
import { Header, IconButton } from "../../components/Header";
import { TutorResponse } from "../../components/TutorResponse";
import {
  GlassGroup,
  GlassSurface,
  useGlass,
  useGlassEdge,
  useTabBarOverlap,
} from "../../components/glass";

type Bubble = ChatMessage & { pending?: boolean; failed?: boolean; imageUri?: string };

const STARTERS = [
  { title: "Explain a topic", prompt: "Explain this like I'm five", icon: "bulb-outline" as const },
  { title: "Quiz me", prompt: "Quiz me on what's due", icon: "help-circle-outline" as const },
  { title: "Make a study plan", prompt: "How should I study for my next test?", icon: "calendar-outline" as const },
  { title: "Break it down", prompt: "Break my hardest assignment into steps", icon: "list-outline" as const },
];

/**
 * Plani, the study tutor.
 *
 * State lives in the component rather than a store: a conversation is one
 * screen's worth of context, and the server already persists it under a
 * conversation_id, so nothing is lost by not duplicating it here.
 */
export default function PlaniScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const glass = useGlass();
  const edge = useGlassEdge();
  const tabOverlap = useTabBarOverlap(insets.bottom);
  const listRef = useRef<FlatList<Bubble>>(null);
  const confirm = useConfirm();

  // Where the tab bar floats, the composer has to sit above it — but only
  // while the keyboard is down; once it is up the keyboard covers the bar.
  const [keyboardUp, setKeyboardUp] = useState(false);
  useEffect(() => {
    if (!tabOverlap) return;
    // Android only emits the `Did` events. Listening to `Will` there leaves
    // the composer lifted above the floating bar after the keyboard closes.
    const showEvent = Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow";
    const hideEvent = Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide";
    const show = Keyboard.addListener(showEvent, () => setKeyboardUp(true));
    const hide = Keyboard.addListener(hideEvent, () => setKeyboardUp(false));
    return () => {
      show.remove();
      hide.remove();
    };
  }, [tabOverlap]);
  const composerLift = tabOverlap && !keyboardUp ? tabOverlap : 0;

  const [messages, setMessages] = useState<Bubble[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [history, setHistory] = useState<Conversation[] | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; id: number } | null>(null);

  const send = useCallback(
    async (text: string, replaceFrom?: number) => {
      const body = text.trim();
      if (!body || busy) return;

      setDraft("");
      setLoadError(null);
      setBusy(true);
      Haptics.selectionAsync().catch(() => {});

      const outgoing: Bubble = { role: "user", content: body };
      // Snapshot before the optimistic append: the request needs the
      // history *including* this message, and reading state back after
      // setState would give the stale array.
      const base = replaceFrom === undefined ? messages : messages.slice(0, replaceFrom);
      const history: ChatMessage[] = [
        ...base.filter((m) => !m.failed).map(({ role, content }) => ({ role, content })),
        { role: "user", content: body },
      ];
      setMessages((prev) => [
        ...prev.slice(0, replaceFrom ?? prev.length),
        outgoing,
        { role: "assistant", content: "", pending: true },
      ]);
      requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));

      try {
        const res = await askPlani(history, conversationId);
        if (res.conversation_id) setConversationId(res.conversation_id);
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = {
            role: "assistant",
            content: res.reply || "…",
            ...(res.study_sources?.length ? { study_sources: res.study_sources.slice(0, 5) } : {}),
          };
          return next;
        });
      } catch (e: any) {
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = {
            role: "assistant",
            content: e?.message || "I couldn't reach the tutor just then. Try again?",
            failed: true,
          };
          return next;
        });
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
      } finally {
        setBusy(false);
        requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
      }
    },
    [busy, messages, conversationId],
  );

  /**
   * Snap & Solve.
   *
   * The picker is asked for base64 directly rather than a file URI: the
   * endpoint wants base64, and reading the file back off disk afterwards
   * is a second failure point for no gain. Quality is dropped to 0.6
   * because a 12-megapixel photo of a worksheet is several megabytes of
   * base64 over a phone connection and reads no better than a small one.
   */
  const snap = useCallback(
    async (from: "camera" | "library") => {
      if (busy) return;

      if (from === "camera" || Platform.OS !== "android") {
        // The Android system photo picker grants temporary access to the
        // selected image. Do not ask for broad gallery access just to attach
        // one worksheet photo. Camera capture still needs its runtime grant.
        const perm =
          from === "camera"
            ? await ImagePicker.requestCameraPermissionsAsync()
            : await ImagePicker.requestMediaLibraryPermissionsAsync();
        if (!perm.granted) {
          await confirm({
            title: "Permission needed",
            message:
              from === "camera"
                ? "Allow camera access in your phone's settings to photograph a problem."
                : "Allow photo access in your phone's settings to pick a problem.",
            actions: [{ label: "OK", cancel: true }],
          });
          return;
        }
      }

      const opts: ImagePicker.ImagePickerOptions = {
        mediaTypes: ["images"],
        quality: 0.6,
        base64: true,
      };
      const res =
        from === "camera"
          ? await ImagePicker.launchCameraAsync(opts)
          : await ImagePicker.launchImageLibraryAsync(opts);

      const asset = res.canceled ? null : res.assets?.[0];
      if (!asset?.base64) return;

      setBusy(true);
      setMessages((prev) => [
        ...prev,
        { role: "user", content: "Solve this for me", imageUri: asset.uri },
        { role: "assistant", content: "", pending: true },
      ]);
      requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));

      try {
        const reply = await askPlaniVision({
          imageBase64: asset.base64,
          mime: asset.mimeType || "image/jpeg",
          question: "Walk me through this step by step.",
          mode: "multi",
          conversationId,
        });
        if (reply.conversation_id) setConversationId(reply.conversation_id);
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = { role: "assistant", content: reply.reply || "…" };
          return next;
        });
      } catch (e: any) {
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = {
            role: "assistant",
            content: e?.message || "I couldn't read that image. Try a clearer photo.",
            failed: true,
          };
          return next;
        });
      } finally {
        setBusy(false);
        requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
      }
    },
    [busy, conversationId],
  );

  const askForPhoto = useCallback(async () => {
    const choice = await confirm({
      title: "Snap & Solve",
      message: "Show Plani the problem and it'll work through every part it can see.",
      actions: [
        { label: "Take a photo" },
        { label: "Choose from library" },
        { label: "Cancel", cancel: true },
      ],
    });
    if (choice === 0) snap("camera");
    else if (choice === 1) snap("library");
  }, [snap, confirm]);

  const openHistory = useCallback(async () => {
    setHistoryOpen(true);
    setLoadingHistory(true);
    setHistoryError(null);
    try {
      setHistory(await getConversations());
    } catch (e: any) {
      setHistory(null);
      setHistoryError(e?.message || "Past chats couldn't load. Check your connection and try again.");
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  const loadConversation = useCallback(async (id: number) => {
    setHistoryOpen(false);
    setLoadError(null);
    setBusy(true);
    try {
      const convo = await getConversation(id);
      setMessages(convo.messages);
      setConversationId(convo.id);
    } catch (e: any) {
      setLoadError({
        id,
        message: e?.message || "That chat wouldn't open. Check your connection and try again.",
      });
    } finally {
      setBusy(false);
    }
  }, []);

  const removeConversation = useCallback(
    async (c: Conversation) => {
      const choice = await confirm({
        title: "Delete this chat?",
        message: c.title,
        actions: [
          { label: "Delete", destructive: true },
          { label: "Cancel", cancel: true },
        ],
      });
      if (choice !== 0) return;
      // Removed from the list first: the request is a formality and waiting
      // on it makes a delete feel broken on a slow connection.
      setHistory((prev) => (prev || []).filter((x) => x.id !== c.id));
      let deleted = false;
      try {
        await deleteConversation(c.id);
        deleted = true;
      } catch {
        const refreshed = await getConversations().catch(() => null);
        if (refreshed) {
          setHistory(refreshed);
        } else {
          setHistory(null);
          setHistoryError("Past chats couldn't refresh. Check your connection and try again.");
        }
      }
      if (deleted && conversationId === c.id) {
        setMessages([]);
        setConversationId(null);
      }
    },
    [conversationId, confirm],
  );

  const reset = useCallback(() => {
    setMessages([]);
    setConversationId(null);
  }, []);

  return (
    <Screen>
      <Header
        title="Plani"
        subtitle="Your study tutor"
        right={
          <View style={{ flexDirection: "row", gap: space.md, alignItems: "center" }}>
            <IconButton icon="time-outline" label="Past chats" onPress={openHistory} />
            {messages.length ? (
              <IconButton icon="create-outline" label="Start a new chat" onPress={reset} />
            ) : null}
          </View>
        }
      />

      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={Platform.OS === "ios" ? 0 : 0}
        style={{ flex: 1 }}
      >
        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(_, i) => String(i)}
          contentContainerStyle={{
            padding: space.lg,
            paddingBottom: space.lg,
            gap: space.sm,
            flexGrow: 1,
          }}
          keyboardDismissMode="interactive"
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
          ListHeaderComponent={
            loadError ? (
              <View style={{ marginBottom: space.sm, gap: space.sm }} accessibilityLiveRegion="polite">
                <Notice text={loadError.message} icon="alert-circle-outline" />
                <Pressable
                  onPress={() => loadConversation(loadError.id)}
                  accessibilityRole="button"
                  accessibilityLabel="Retry opening this chat"
                  style={{ alignSelf: "flex-start", minHeight: 44, justifyContent: "center" }}
                >
                  <T variant="sm" weight="700" tone="accent">Try opening again</T>
                </Pressable>
              </View>
            ) : null
          }
          ListEmptyComponent={
            <View style={{ flex: 1, justifyContent: "center", gap: space.xl, paddingVertical: space.lg }}>
              <View style={{ alignItems: "center", gap: space.sm, paddingHorizontal: space.sm }}>
                <GlassSurface
                  tint={colors.accentSoft}
                  style={{
                    width: 72,
                    height: 72,
                    borderRadius: radius.xl,
                    backgroundColor: colors.accentSoft,
                    alignItems: "center",
                    justifyContent: "center",
                    marginBottom: space.xs,
                  }}
                >
                  <Ionicons name="sparkles" size={30} color={colors.accent} />
                </GlassSurface>
                <T variant="lg" weight="700" style={{ textAlign: "center" }}>
                  Ask Plani anything
                </T>
                <T tone="muted" variant="sm" style={{ textAlign: "center", maxWidth: 320, lineHeight: 21 }}>
                  Get help with your courses, plan what to study, or work through a problem step by step.
                </T>
              </View>

              <View style={{ gap: space.sm }}>
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 2 }}>
                  <Label>QUICK START</Label>
                  <T variant="xs" tone="muted">Tap to ask</T>
                </View>
                <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm }}>
                  {STARTERS.map((starter) => (
                    <Pressable
                      key={starter.title}
                      onPress={() => send(starter.prompt)}
                      disabled={busy}
                      accessibilityRole="button"
                      accessibilityLabel={starter.title}
                      accessibilityHint={`Sends: ${starter.prompt}`}
                      style={({ pressed }) => ({
                        width: "47%",
                        minHeight: 68,
                        opacity: busy ? 0.55 : pressed ? 0.76 : 1,
                      })}
                    >
                      <Card style={{
                        flex: 1,
                        minHeight: 68,
                        paddingHorizontal: space.md,
                        paddingVertical: space.sm,
                        flexDirection: "row",
                        alignItems: "center",
                        gap: space.sm,
                        borderColor: colors.borderAccent,
                        backgroundColor: colors.bgSecondary,
                      }}>
                        <View style={{
                          width: 32,
                          height: 32,
                          borderRadius: radius.md,
                          alignItems: "center",
                          justifyContent: "center",
                          backgroundColor: colors.accentSoft,
                        }}>
                          <Ionicons name={starter.icon} size={17} color={colors.accent} />
                        </View>
                        <T variant="xs" weight="700" style={{ flex: 1, lineHeight: 17 }}>
                          {starter.title}
                        </T>
                      </Card>
                    </Pressable>
                  ))}
                </View>
                <Pressable
                  onPress={askForPhoto}
                  disabled={busy}
                  accessibilityRole="button"
                  accessibilityLabel="Take or choose a photo of a study problem"
                  accessibilityHint="Opens the camera or your photo library"
                  style={({ pressed }) => ({
                    minHeight: 48,
                    marginTop: space.xs,
                    paddingHorizontal: space.md,
                    borderRadius: radius.lg,
                    flexDirection: "row",
                    alignItems: "center",
                    gap: space.sm,
                    backgroundColor: colors.accentSoft,
                    opacity: busy ? 0.55 : pressed ? 0.75 : 1,
                  })}
                >
                  <Ionicons name="camera-outline" size={19} color={colors.accent} />
                  <T variant="sm" weight="700" tone="accent" style={{ flex: 1 }}>
                    Get help with a photo
                  </T>
                  <Ionicons name="arrow-forward" size={16} color={colors.accent} />
                </Pressable>
              </View>
            </View>
          }
          renderItem={({ item, index }) => (
            <MessageBubble
              msg={item}
              onRetry={
                item.failed && index > 0 && messages[index - 1]?.role === "user"
                  ? () => send(messages[index - 1].content, index - 1)
                  : undefined
              }
            />
          )}
        />

        {/* On glass the bar itself disappears and its three controls float
            over the conversation as one merged cluster, the iMessage shape. */}
        <GlassGroup
          spacing={space.sm}
          style={{
            flexDirection: "row",
            gap: space.sm,
            alignItems: "flex-end",
            paddingHorizontal: space.lg,
            paddingTop: space.sm,
            paddingBottom: (insets.bottom > 0 ? space.sm : space.md) + composerLift,
            borderTopWidth: glass ? 0 : 1,
            borderTopColor: colors.border,
            backgroundColor: glass ? "transparent" : colors.bg,
          }}
        >
          <Pressable
            onPress={askForPhoto}
            disabled={busy}
            accessibilityRole="button"
            accessibilityLabel="Snap and solve a problem"
            style={{ opacity: busy ? 0.5 : 1 }}
          >
            <GlassSurface
              interactive
              style={{
                width: 44,
                height: 44,
                borderRadius: radius.pill,
                backgroundColor: colors.bgElevated,
                borderWidth: glass ? StyleSheet.hairlineWidth : 0,
                borderColor: edge,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Ionicons
                name="camera-outline"
                size={21}
                color={glass ? colors.textPrimary : colors.textSecondary}
              />
            </GlassSurface>
          </Pressable>

          <GlassSurface
            style={{
              flex: 1,
              borderWidth: glass ? StyleSheet.hairlineWidth : 1,
              borderColor: glass ? edge : colors.border,
              backgroundColor: colors.bgSecondary,
              borderRadius: radius.xl,
            }}
          >
            <TextInput
              value={draft}
              onChangeText={setDraft}
              placeholder="Ask about anything you're studying…"
              placeholderTextColor={colors.textMuted}
              multiline
              editable={!busy}
              accessibilityLabel="Message Plani"
              accessibilityHint="Type a question, then use Send to get help with your studies"
              returnKeyType="send"
              submitBehavior="submit"
              onSubmitEditing={() => send(draft)}
              style={{
                maxHeight: 120,
                minHeight: 44,
                paddingHorizontal: 16,
                paddingTop: Platform.OS === "ios" ? 12 : 8,
                paddingBottom: Platform.OS === "ios" ? 12 : 8,
                fontSize: 16,
                color: colors.textPrimary,
              }}
            />
          </GlassSurface>

          <Pressable
            onPress={() => send(draft)}
            disabled={busy || !draft.trim()}
            accessibilityRole="button"
            accessibilityLabel="Send"
            accessibilityHint={busy ? "Plani is preparing a reply" : "Sends your message to Plani"}
            accessibilityState={{ disabled: busy || !draft.trim(), busy }}
          >
            <GlassSurface
              interactive
              tint={draft.trim() && !busy ? colors.accent : undefined}
              style={{
                width: 44,
                height: 44,
                borderRadius: radius.pill,
                backgroundColor: draft.trim() && !busy ? colors.accent : colors.bgElevated,
                borderWidth: glass ? StyleSheet.hairlineWidth : 0,
                borderColor: edge,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {busy ? (
                <ActivityIndicator size="small" color={colors.textMuted} />
              ) : (
                <Ionicons
                  name="arrow-up"
                  size={20}
                  color={draft.trim() ? colors.onAccent : colors.textMuted}
                />
              )}
            </GlassSurface>
          </Pressable>
        </GlassGroup>
      </KeyboardAvoidingView>

      <Modal
        visible={historyOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setHistoryOpen(false)}
      >
        <Pressable
          style={{ flex: 1, backgroundColor: colors.scrim }}
          onPress={() => setHistoryOpen(false)}
          accessibilityRole="button"
          accessibilityLabel="Close past chats"
        />
        <GlassSurface
          style={{
            maxHeight: "68%",
            backgroundColor: colors.bg,
            borderTopLeftRadius: radius.xl + 10,
            borderTopRightRadius: radius.xl + 10,
            borderTopWidth: glass ? StyleSheet.hairlineWidth : 0,
            borderColor: edge,
            paddingTop: space.lg,
            paddingBottom: insets.bottom + space.lg,
          }}
        >
          <View
            style={{
              flexDirection: "row",
              alignItems: "center",
              paddingHorizontal: space.lg,
              paddingBottom: space.md,
            }}
          >
            <T variant="md" weight="700" style={{ flex: 1 }}>
              Past chats
            </T>
            <Pressable
              onPress={() => setHistoryOpen(false)}
              hitSlop={10}
              accessibilityRole="button"
              accessibilityLabel="Close"
            >
              <Ionicons name="close" size={24} color={colors.textSecondary} />
            </Pressable>
          </View>

          {loadingHistory ? (
            <View style={{ paddingVertical: space.xxl }}>
              <Loading />
            </View>
          ) : historyError ? (
            <ErrorState message={historyError} onRetry={openHistory} />
          ) : history?.length ? (
            <ScrollView contentContainerStyle={{ paddingHorizontal: space.lg, gap: space.sm }}>
              {history.map((c) => (
                <Card
                  key={c.id}
                  style={{
                    padding: space.md,
                    flexDirection: "row",
                    alignItems: "center",
                    gap: space.md,
                    borderColor: c.id === conversationId ? colors.borderAccent : colors.border,
                  }}
                >
                  <Pressable
                    onPress={() => loadConversation(c.id)}
                    accessibilityRole="button"
                    accessibilityLabel={`Open ${c.title || "Untitled chat"}`}
                    style={{ flex: 1, flexDirection: "row", alignItems: "center", gap: space.md }}
                  >
                    <Ionicons name="chatbubble-outline" size={17} color={colors.textMuted} />
                    <T variant="sm" style={{ flex: 1 }} numberOfLines={1}>
                      {c.title || "Untitled chat"}
                    </T>
                  </Pressable>
                  <Pressable
                    onPress={() => removeConversation(c)}
                    hitSlop={10}
                    accessibilityRole="button"
                    accessibilityLabel={`Delete ${c.title || "Untitled chat"}`}
                    accessibilityHint="Deletes this conversation without opening it"
                  >
                    <Ionicons name="trash-outline" size={17} color={colors.textMuted} />
                  </Pressable>
                </Card>
              ))}
            </ScrollView>
          ) : (
            <EmptyState
              icon="chatbubbles-outline"
              title="No past chats"
              body="Conversations you have with Plani show up here."
            />
          )}
        </GlassSurface>
      </Modal>
    </Screen>
  );
}

function MessageBubble({ msg, onRetry }: { msg: Bubble; onRetry?: () => void }) {
  const { colors } = useTheme();
  const mine = msg.role === "user";

  return (
    <View
      style={{
        alignSelf: mine ? "flex-end" : "flex-start",
        maxWidth: "90%",
        gap: space.xs,
      }}
      accessibilityLiveRegion={msg.pending || msg.failed ? "polite" : "none"}
    >
      {!mine ? (
        <View style={{ flexDirection: "row", alignItems: "center", gap: space.xs, paddingLeft: space.xs }}>
          <Ionicons name="sparkles" size={13} color={colors.accent} />
          <T variant="xs" weight="700" tone="accent">Plani</T>
        </View>
      ) : null}
      <View
        style={{
          backgroundColor: mine ? colors.accent : colors.bgCard,
          borderWidth: mine ? 0 : 1,
          borderColor: msg.failed ? colors.danger : colors.border,
          borderRadius: radius.xl,
          // The corner nearest the sender is squared off, which makes a stack
          // of bubbles read as a conversation rather than a list.
          borderBottomRightRadius: mine ? radius.sm : radius.xl,
          borderBottomLeftRadius: mine ? radius.xl : radius.sm,
          paddingHorizontal: space.md,
          paddingVertical: space.md - 2,
        }}
      >
        {msg.imageUri ? (
          <Image
            source={{ uri: msg.imageUri }}
            style={{
              width: 200,
              height: 150,
              borderRadius: radius.md,
              marginBottom: space.sm,
              backgroundColor: colors.bgElevated,
            }}
            resizeMode="cover"
            accessibilityLabel="The problem you sent"
          />
        ) : null}
        {msg.pending ? (
          <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
            <ActivityIndicator size="small" color={colors.accent} />
            <T variant="sm" tone="muted">
              Plani is thinking…
            </T>
          </View>
        ) : (
          <>
            {mine || msg.failed ? (
              <T variant="base" tone={mine ? "onAccent" : "danger"} style={{ lineHeight: 22 }} selectable>
                {msg.content}
              </T>
            ) : <TutorResponse text={msg.content} />}
            {!mine && !msg.failed && msg.study_sources?.length ? (
              <View
                accessibilityLabel="Notes Plani used for this answer"
                style={{ marginTop: space.sm, paddingTop: space.sm, borderTopWidth: 1, borderTopColor: colors.border, gap: space.xs }}
              >
                <View style={{ flexDirection: "row", alignItems: "center", gap: space.xs }}>
                  <Ionicons name="document-text-outline" size={14} color={colors.textMuted} />
                  <T variant="xs" weight="700" tone="muted">From your notes</T>
                </View>
                {msg.study_sources.slice(0, 5).map((source, index) => (
                  <View key={`${source.note_id}-${index}`} style={{ flexDirection: "row", alignItems: "flex-start", gap: space.xs }}>
                    <T variant="xs" weight="700" tone="accent">N{index + 1}</T>
                    <T variant="xs" tone="muted" style={{ flex: 1, lineHeight: 18 }}>
                      {[source.course, source.title].filter(Boolean).join(" · ") || "Course notes"}
                    </T>
                  </View>
                ))}
              </View>
            ) : null}
            {msg.failed ? (
              <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: space.md, marginTop: 6 }}>
                <Chip label="Not sent" icon="alert-circle-outline" fg={colors.dangerText} bg={colors.dangerSoft} />
                {onRetry ? (
                  <Pressable
                    onPress={onRetry}
                    accessibilityRole="button"
                    accessibilityLabel="Retry message"
                    accessibilityHint="Sends your question to Plani again"
                    style={{ minHeight: 44, justifyContent: "center", paddingHorizontal: space.xs }}
                  >
                    <T variant="sm" weight="700" tone="danger">Retry</T>
                  </Pressable>
                ) : null}
              </View>
            ) : null}
          </>
        )}
      </View>
    </View>
  );
}
