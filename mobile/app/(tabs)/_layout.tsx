import React from "react";
import { Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { GlassView, isGlassEffectAPIAvailable } from "expo-glass-effect";
import { Platform, StyleSheet } from "react-native";
import { useTheme } from "../../theme/ThemeProvider";

/**
 * Tab order follows the day, not the data model: what is due, then the
 * plan for doing it, then where that leaves your grades, then help. Today
 * stays first because it is the answer to "what now", which is why anyone
 * opens a planner on a phone at all.
 */
export default function TabsLayout() {
  const { colors, scheme } = useTheme();
  const supportsGlass = Platform.OS === "ios" && isGlassEffectAPIAvailable();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarStyle: {
          backgroundColor: supportsGlass ? "transparent" : colors.navBg,
          borderTopColor: colors.border,
          // The default 49pt bar crowds a five-icon row once the labels
          // are on; iOS adds the home-indicator inset on top of this.
          height: Platform.OS === "ios" ? 84 : 62,
          paddingTop: 6,
        },
        tabBarBackground: supportsGlass
          ? () => (
              <GlassView
                glassEffectStyle="regular"
                colorScheme={scheme}
                tintColor={scheme === "dark" ? "rgba(16, 16, 18, 0.72)" : "rgba(255, 255, 255, 0.72)"}
                style={StyleSheet.absoluteFill}
              />
            )
          : undefined,
        tabBarLabelStyle: { fontSize: 11, fontWeight: "600" },
      }}
    >
      <Tabs.Screen
        name="today"
        options={{
          title: "Today",
          tabBarIcon: ({ color, size }) => <Ionicons name="sunny-outline" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="tasks"
        options={{
          title: "Due",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="checkbox-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="plan"
        options={{
          title: "Plan",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="calendar-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="grades"
        options={{
          title: "Grades",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="stats-chart-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="plani"
        options={{
          title: "Plani",
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="chatbubble-ellipses-outline" size={size} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}
