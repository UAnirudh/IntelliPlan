import React from "react";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { View } from "react-native";
import { AuthProvider } from "../lib/auth";
import { ConfirmProvider } from "../components/Confirm";
import { ThemeProvider, useTheme } from "../theme/ThemeProvider";

function Shell() {
  const { colors, scheme } = useTheme();
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
