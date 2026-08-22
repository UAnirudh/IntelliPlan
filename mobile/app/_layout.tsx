import React from "react";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AppState, View } from "react-native";
import { AuthProvider } from "../lib/auth";
import { ConfirmProvider } from "../components/Confirm";
import { flushQueue } from "../lib/queue";
import { AppErrorBoundary } from "../components/ErrorBoundary";
import { ThemeProvider, useTheme } from "../theme/ThemeProvider";

function Shell() {
  const { colors, scheme } = useTheme();

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
        <Stack.Screen name="onboarding" />
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="settings" options={{ presentation: "modal" }} />
        <Stack.Screen name="new-task" options={{ presentation: "modal" }} />
        <Stack.Screen name="task" options={{ presentation: "modal" }} />
        <Stack.Screen name="connect" options={{ presentation: "modal" }} />
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
