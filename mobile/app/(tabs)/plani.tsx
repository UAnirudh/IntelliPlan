import React, { useCallback, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import * as ImagePicker from "expo-image-picker";
import { askPlani, askPlaniVision, ChatMessage } from "../../lib/api";
import { useTheme } from "../../theme/ThemeProvider";
import { radius, space } from "../../theme/tokens";
import { Card, Chip, Label, Screen, T } from "../../components/ui";
import { Header, IconButton } from "../../components/Header";

type Bubble = ChatMessage & { pending?: boolean; failed?: boolean; imageUri?: string };

const STARTERS = [
  "Explain this like I'm five",
  "Quiz me on what's due",
  "How should I study for my next test?",
  "Break my hardest assignment into steps",
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
  const listRef = useRef<FlatList<Bubble>>(null);

  const [messages, setMessages] = useState<Bubble[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<number | null>(null);

  const send = useCallback(
    async (text: string) => {
      const body = text.trim();
      if (!body || busy) return;

      setDraft("");
      setBusy(true);
      Haptics.selectionAsync().catch(() => {});

      const outgoing: Bubble = { role: "user", content: body };
      // Snapshot before the optimistic append: the request needs the
      // history *including* this message, and reading state back after
      // setState would give the stale array.
      const history: ChatMessage[] = [
        ...messages.filter((m) => !m.failed).map(({ role, content }) => ({ role, content })),
        { role: "user", content: body },
      ];
      setMessages((prev) => [...prev, outgoing, { role: "assistant", content: "", pending: true }]);
      requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));

      try {
        const res = await askPlani(history, conversationId);
        if (res.conversation_id) setConversationId(res.conversation_id);
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = { role: "assistant", content: res.reply || "…" };
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

      const perm =
        from === "camera"
          ? await ImagePicker.requestCameraPermissionsAsync()
          : await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!perm.granted) {
        Alert.alert(
          "Permission needed",
          from === "camera"
            ? "Allow camera access to photograph a problem."
            : "Allow photo access to pick a problem.",
        );
        return;
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

  const askForPhoto = useCallback(() => {
    Alert.alert("Snap & Solve", "Show Plani the problem and it'll work through it.", [
      { text: "Take a photo", onPress: () => snap("camera") },
      { text: "Choose from library", onPress: () => snap("library") },
      { text: "Cancel", style: "cancel" },
    ]);
  }, [snap]);

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
          messages.length ? (
            <IconButton icon="create-outline" label="Start a new chat" onPress={reset} />
          ) : undefined
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
          ListEmptyComponent={
            <View style={{ flex: 1, justifyContent: "center", gap: space.lg }}>
              <View style={{ alignItems: "center", gap: space.sm }}>
                <View
                  style={{
                    width: 60,
                    height: 60,
                    borderRadius: radius.pill,
                    backgroundColor: colors.accentSoft,
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Ionicons name="sparkles" size={26} color={colors.accent} />
                </View>
                <T variant="md" weight="700">
                  Ask Plani anything
                </T>
                <T tone="muted" variant="sm" style={{ textAlign: "center", maxWidth: 300 }}>
                  Plani knows your courses and what's due. Tap the camera to
                  photograph a worksheet and it works through every problem.
                </T>
              </View>

              <View style={{ gap: space.sm }}>
                <Label>Try one of these</Label>
                {STARTERS.map((s) => (
                  <Pressable key={s} onPress={() => send(s)} accessibilityRole="button">
                    <Card style={{ padding: space.md, flexDirection: "row", alignItems: "center", gap: space.sm }}>
                      <Ionicons name="arrow-forward-circle-outline" size={18} color={colors.accent} />
                      <T variant="sm" style={{ flex: 1 }}>
                        {s}
                      </T>
                    </Card>
                  </Pressable>
                ))}
              </View>
            </View>
          }
          renderItem={({ item }) => <MessageBubble msg={item} />}
        />

        <View
          style={{
            flexDirection: "row",
            gap: space.sm,
            alignItems: "flex-end",
            paddingHorizontal: space.lg,
            paddingTop: space.sm,
            paddingBottom: insets.bottom > 0 ? space.sm : space.md,
            borderTopWidth: 1,
            borderTopColor: colors.border,
            backgroundColor: colors.bg,
          }}
        >
          <Pressable
            onPress={askForPhoto}
            disabled={busy}
            accessibilityRole="button"
            accessibilityLabel="Snap and solve a problem"
            style={{
              width: 44,
              height: 44,
              borderRadius: radius.pill,
              backgroundColor: colors.bgElevated,
              alignItems: "center",
              justifyContent: "center",
              opacity: busy ? 0.5 : 1,
            }}
          >
            <Ionicons name="camera-outline" size={21} color={colors.textSecondary} />
          </Pressable>

          <TextInput
            value={draft}
            onChangeText={setDraft}
            placeholder="Ask about anything you're studying…"
            placeholderTextColor={colors.textMuted}
            multiline
            style={{
              flex: 1,
              maxHeight: 120,
              minHeight: 44,
              borderWidth: 1,
              borderColor: colors.border,
              backgroundColor: colors.bgSecondary,
              borderRadius: radius.xl,
              paddingHorizontal: 16,
              paddingTop: Platform.OS === "ios" ? 12 : 8,
              paddingBottom: Platform.OS === "ios" ? 12 : 8,
              fontSize: 16,
              color: colors.textPrimary,
            }}
          />
          <Pressable
            onPress={() => send(draft)}
            disabled={busy || !draft.trim()}
            accessibilityRole="button"
            accessibilityLabel="Send"
            style={{
              width: 44,
              height: 44,
              borderRadius: radius.pill,
              backgroundColor: draft.trim() && !busy ? colors.accent : colors.bgElevated,
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
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </Screen>
  );
}

function MessageBubble({ msg }: { msg: Bubble }) {
  const { colors } = useTheme();
  const mine = msg.role === "user";

  return (
    <View
      style={{
        alignSelf: mine ? "flex-end" : "flex-start",
        maxWidth: "88%",
        backgroundColor: mine ? colors.accent : colors.bgCard,
        borderWidth: mine ? 0 : 1,
        borderColor: msg.failed ? colors.danger : colors.border,
        borderRadius: radius.xl,
        // The corner nearest the sender is squared off, which is what makes
        // a stack of bubbles read as a conversation rather than a list.
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
          <T
            variant="base"
            tone={mine ? "onAccent" : msg.failed ? "danger" : "primary"}
            style={{ lineHeight: 22 }}
            selectable
          >
            {msg.content}
          </T>
          {msg.failed ? (
            <Chip label="Not sent" icon="alert-circle-outline" fg={colors.dangerText} bg={colors.dangerSoft} style={{ marginTop: 6 }} />
          ) : null}
        </>
      )}
    </View>
  );
}
