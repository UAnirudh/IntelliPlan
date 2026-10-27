import React, { useCallback, useEffect, useState } from "react";
import { Modal, Pressable, StyleSheet, View } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Ionicons } from "@expo/vector-icons";
import { getTasks } from "../lib/api";
import { areRemindersEnabled, setRemindersEnabled, syncReminders } from "../lib/reminders";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, T } from "./ui";

const PROMPT_KEY = (userId: number | string) => `ip_reminder_prompt_answered_${userId}`;

/**
 * Ask once, after a student has reached Today and seen the app's context.
 * This requests only local deadline reminders; server nudges are separate
 * and remain available from Settings when the installed build supports it.
 */
export function ReminderOptInPrompt({ userId }: { userId?: number | string }) {
  const { colors } = useTheme();
  const [visible, setVisible] = useState(false);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    if (userId === undefined) {
      setChecking(false);
      return () => { active = false; };
    }

    (async () => {
      try {
        const key = PROMPT_KEY(userId);
        const answered = await AsyncStorage.getItem(key);
        if (answered) return;
        if (await areRemindersEnabled()) {
          await AsyncStorage.setItem(key, "enabled");
          return;
        }
        // Show an in-app explanation first. The OS permission dialog is only
        // requested by the explicit Enable button below.
        if (active) setVisible(true);
      } catch {
        // Storage/OS failures should not block Today. Settings always has a
        // second path to enable reminders.
      } finally {
        if (active) setChecking(false);
      }
    })();

    return () => { active = false; };
  }, [userId]);

  const dismiss = useCallback(async () => {
    if (userId !== undefined) {
      await AsyncStorage.setItem(PROMPT_KEY(userId), "dismissed").catch(() => {});
    }
    setVisible(false);
  }, [userId]);

  const enable = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const enabled = await setRemindersEnabled(true);
      // A clear yes/no answer from the system dialog should not trigger the
      // same prompt on every later launch. The Settings switch remains usable.
      if (userId !== undefined) {
        await AsyncStorage.setItem(PROMPT_KEY(userId), enabled ? "enabled" : "dismissed");
      }
      if (!enabled) {
        setError("Notifications are off in your phone settings. You can turn them on later in IntelliPlan Settings.");
        return;
      }
      try {
        await syncReminders(await getTasks());
      } catch {
        // Tasks are refreshed on the Due screen and reminders sync then.
      }
      setVisible(false);
    } catch {
      setError("We couldn't set up reminders right now. Try again, or enable them later in Settings.");
    } finally {
      setBusy(false);
    }
  }, [busy, userId]);

  if (checking || !visible) return null;

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      statusBarTranslucent
      onRequestClose={dismiss}
    >
      <View style={styles.backdrop}>
        <View
          accessibilityViewIsModal
          accessibilityLabel="Enable deadline reminders"
          style={[styles.card, { backgroundColor: colors.bgCard, borderColor: colors.border }]}
        >
          <View style={[styles.icon, { backgroundColor: colors.accentSoft }]}>
            <Ionicons name="notifications-outline" size={27} color={colors.accent} />
          </View>
          <T variant="xl" weight="700" style={styles.title}>Keep deadlines in view</T>
          <T tone="secondary" style={styles.body}>
            Get one gentle reminder on your phone the evening before work is due. It is scheduled on this device, so it still works offline and when IntelliPlan is closed.
          </T>
          <View style={[styles.detail, { backgroundColor: colors.bgElevated }]}>
            <Ionicons name="lock-closed-outline" size={16} color={colors.textMuted} />
            <T variant="xs" tone="muted" style={styles.detailText}>
              Only tasks you've synced to this phone are scheduled. Change or turn off reminders anytime in Settings.
            </T>
          </View>
          <T variant="xs" tone="muted" style={styles.serverNote}>
            Server nudges are separate and need this app's remote-push setup. Check Settings if you want to use them.
          </T>
          {error ? <T variant="xs" tone="warn" accessibilityRole="alert">{error}</T> : null}
          <Button
            title="Enable deadline reminders"
            icon="notifications"
            busy={busy}
            onPress={enable}
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Not now. You can enable reminders later in Settings."
            disabled={busy}
            hitSlop={8}
            onPress={dismiss}
            style={styles.notNow}
          >
            <T variant="sm" tone="muted" weight="600">Not now</T>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: "center",
    padding: space.lg,
    backgroundColor: "rgba(5, 12, 24, 0.62)",
  },
  card: {
    alignSelf: "center",
    width: "100%",
    maxWidth: 420,
    borderWidth: 1,
    borderRadius: radius.xl,
    padding: space.lg,
    gap: space.md,
  },
  icon: {
    width: 54,
    height: 54,
    borderRadius: radius.lg,
    alignItems: "center",
    justifyContent: "center",
  },
  title: { marginTop: space.xs },
  body: { lineHeight: 23 },
  detail: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: space.sm,
    padding: space.md,
    borderRadius: radius.md,
  },
  detailText: { flex: 1, lineHeight: 18 },
  serverNote: { lineHeight: 18 },
  notNow: { alignItems: "center", justifyContent: "center", minHeight: 44 },
});
