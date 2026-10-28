import React from "react";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AppState, View } from "react-native";
import * as Notifications from "expo-notifications";
import { Href, useRouter } from "expo-router";
import { AuthProvider } from "../lib/auth";
import { useAuth } from "../lib/auth";
import { ConfirmProvider } from "../components/Confirm";
import { flushQueue } from "../lib/queue";
import { getTasks } from "../lib/api";
import { areRemindersEnabled, getReminderOperation, isReminderOperationCurrent, syncReminders } from "../lib/reminders";
import { AppErrorBoundary } from "../components/ErrorBoundary";
import { ThemeProvider, useTheme } from "../theme/ThemeProvider";

function notificationHref(data: Record<string, unknown>): Href | null {
  const task = data.task;
  if (task && typeof task === "object" && !Array.isArray(task)) {
    const raw = task as Record<string, unknown>;
    if (typeof raw.title === "string" && raw.title.trim()) {
      const details = {
        title: raw.title.slice(0, 300),
        course: typeof raw.course === "string" ? raw.course.slice(0, 200) : "",
        due_date: typeof raw.due_date === "string" ? raw.due_date.slice(0, 80) : "",
        priority: typeof raw.priority === "string" ? raw.priority.slice(0, 40) : "",
        estimated_time: typeof raw.estimated_time === "number" ? raw.estimated_time : undefined,
        notes: typeof raw.notes === "string" ? raw.notes.slice(0, 2000) : "",
        dismissed: raw.dismissed === true,
        id: typeof raw.id === "string" || typeof raw.id === "number" ? raw.id : undefined,
      };
      return { pathname: "/task", params: { data: JSON.stringify(details) } };
    }
  }

  // Push payloads are external input. Accept only known same-app routes;
  // never send a notification tap to an arbitrary URL or path.
  const rawUrl = typeof data.url === "string" ? data.url : "";
  let path = "";
  try {
    const parsed = new URL(rawUrl, "https://intelliplan.tech");
    if (parsed.origin !== "https://intelliplan.tech") return null;
    path = parsed.pathname;
  } catch {
    return null;
  }
  if (["/", "/active", "/dashboard"].includes(path)) return "/(tabs)/today";
  if (["/tasks", "/assignments"].includes(path)) return "/(tabs)/tasks";
  return null;
}

function Shell() {
  const { colors, scheme } = useTheme();
  const router = useRouter();
  const { user, booting } = useAuth();

  // Expo delivers taps to the listener while the app is alive and preserves
  // the last response when a notification launches a cold app. De-dupe the
  // two sources so the initial lookup and listener cannot push twice.
  const [notificationResponse, setNotificationResponse] = React.useState<
    Notifications.NotificationResponse | null
  >(null);
  const responseKey = React.useRef<string | null>(null);
  React.useEffect(() => {
    const receive = (response: Notifications.NotificationResponse | null) => {
      if (!response) return;
      const key = `${response.notification.request.identifier}:${response.notification.date}:${response.actionIdentifier}`;
      if (responseKey.current === key) return;
      responseKey.current = key;
      setNotificationResponse(response);
    };
    const subscription = Notifications.addNotificationResponseReceivedListener(receive);
    Notifications.getLastNotificationResponseAsync().then(receive).catch(() => {});
    return () => subscription.remove();
  }, []);

  React.useEffect(() => {
    if (!notificationResponse || booting) return;
    const response = notificationResponse;
    setNotificationResponse(null);
    Notifications.clearLastNotificationResponseAsync().catch(() => {});

    // The app's normal index/auth gate handles signed-out users. Do not
    // navigate them into private task screens before a session exists.
    if (!user || response.actionIdentifier === "dismiss") {
      if (!user) router.replace("/");
      return;
    }
    const data = response.notification.request.content.data;
    if (!data || typeof data !== "object" || Array.isArray(data)) return;
    const href = notificationHref(data as Record<string, unknown>);
    if (href) router.push(href);
  }, [notificationResponse, booting, user, router]);

  // Foregrounding is the other moment the network may have come back —
  // the Due tab flushes when its list loads, but the student may not open
  // that tab, and the edits should not wait for them to.
  React.useEffect(() => {
    const sub = AppState.addEventListener("change", (s) => {
      if (s === "active") flushQueue().catch(() => {});
    });
    flushQueue().catch(() => {});
    return () => sub.remove();
  }, []);

  // Keep local reminders current from the app shell, even when someone uses
  // Today, Tutor, or another tab without opening Due. Local notifications
  // survive a closed app, so refresh the server's task list at startup and
  // whenever the app returns to the foreground, then rebuild the OS schedule.
  // The operation token prevents a slow response from an older session or
  // reminder setting from overwriting the current account's schedule.
  React.useEffect(() => {
    if (booting || !user) return;

    let active = true;
    let syncing = false;
    let refreshRequested = false;
    const refreshReminders = async () => {
      if (!active) return;
      if (syncing) {
        refreshRequested = true;
        return;
      }
      const operation = getReminderOperation();
      if (operation < 0 || !isReminderOperationCurrent(operation)) return;
      syncing = true;
      try {
        if (!(await areRemindersEnabled()) || !isReminderOperationCurrent(operation)) return;
        const tasks = await getTasks();
        if (active && isReminderOperationCurrent(operation)) {
          await syncReminders(tasks, operation);
        }
      } catch {
        // A transient network failure must not block app navigation. The
        // last successful on-device schedule remains available offline.
      } finally {
        syncing = false;
        if (active && refreshRequested) {
          refreshRequested = false;
          void refreshReminders();
        }
      }
    };

    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") void refreshReminders();
    });
    void refreshReminders();

    return () => {
      active = false;
      subscription.remove();
    };
  }, [booting, user?.id]);

  return (
    // The stack animates between screens over whatever is behind it; without
    // a painted root that is white, which flashes on every push in dark mode.
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <StatusBar style={scheme === "dark" ? "light" : "dark"} />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: colors.bg },
        }}
      >
        <Stack.Screen name="index" />
        <Stack.Screen name="login" />
        <Stack.Screen name="age-setup" />
        <Stack.Screen name="parent-consent-pending" />
        <Stack.Screen name="email-verification" />
        <Stack.Screen name="onboarding" />
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="settings" options={{ presentation: "modal" }} />
        <Stack.Screen name="new-task" options={{ presentation: "modal" }} />
        <Stack.Screen name="task" options={{ presentation: "modal" }} />
        <Stack.Screen name="edit-task" options={{ presentation: "modal" }} />
        <Stack.Screen name="connect" options={{ presentation: "modal" }} />
        <Stack.Screen name="plan-custom" options={{ presentation: "modal" }} />
        <Stack.Screen name="study" options={{ presentation: "modal" }} />
        <Stack.Screen name="streak" options={{ presentation: "modal" }} />
        <Stack.Screen name="accounts" options={{ presentation: "modal" }} />
        {/* Full screen, not a card: a session should not have a dismiss
            gesture sitting under the student's thumb, and the timer is the
            only thing that matters while it runs. */}
        <Stack.Screen name="focus" options={{ presentation: "fullScreenModal" }} />
      </Stack>
    </View>
  );
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <AuthProvider>
          <ConfirmProvider>
            <Shell />
          </ConfirmProvider>
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}

/**
 * expo-router picks this up by name. Exported from the root layout so it
 * covers every screen, and wrapped in ThemeProvider because the boundary
 * renders outside the tree the providers below it set up — without that it
 * would throw looking up a theme while handling a throw.
 */
export function ErrorBoundary(props: { error: Error; retry: () => Promise<void> }) {
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <AppErrorBoundary {...props} />
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
