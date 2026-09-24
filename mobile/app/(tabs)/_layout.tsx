import React from "react";
import { Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { Platform, StyleSheet } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  FLOATING_TAB_HEIGHT,
  FLOATING_TAB_INSET,
  floatingTabBottom,
  GlassSurface,
  useGlass,
  useGlassEdge,
} from "../../components/glass";
import { useTheme } from "../../theme/ThemeProvider";

/**
 * Tab order follows the day, not the data model: what is due, then the
 * plan for doing it, then where that leaves your grades, then help. Today
 * stays first because it is the answer to "what now", which is why anyone
 * opens a planner on a phone at all.
 *
 * Where Liquid Glass exists the bar floats as a capsule above the content,
 * the iOS 26 tab bar shape, and lists scroll on underneath it — every tab
 * already pads its list by the home-indicator inset plus 100pt, which
 * clears the capsule. Elsewhere it stays the docked bar it always was.
 */
export default function TabsLayout() {
  const { colors } = useTheme();
  const glass = useGlass();
  const edge = useGlassEdge();
  const insets = useSafeAreaInsets();

  const floating = {
    position: "absolute" as const,
    left: FLOATING_TAB_INSET,
    right: FLOATING_TAB_INSET,
    bottom: floatingTabBottom(insets.bottom),
    height: FLOATING_TAB_HEIGHT,
    paddingTop: 8,
    paddingBottom: 8,
    borderRadius: FLOATING_TAB_HEIGHT / 2,
    borderTopWidth: 0,
    backgroundColor: "transparent",
    // No shadow: on a transparent view iOS casts it from each icon instead.
    elevation: 0,
  };

  const docked = {
    backgroundColor: colors.navBg,
    borderTopColor: colors.border,
    // The default 49pt bar crowds a five-icon row once the labels
    // are on; iOS adds the home-indicator inset on top of this.
    height: Platform.OS === "ios" ? 84 : 62,
    paddingTop: 6,
  };

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarStyle: glass ? floating : docked,
        tabBarItemStyle: glass ? { borderRadius: FLOATING_TAB_HEIGHT / 2 } : undefined,
        tabBarBackground: glass
          ? () => (
              <GlassSurface
                interactive
                style={[
                  StyleSheet.absoluteFill,
                  {
                    borderRadius: FLOATING_TAB_HEIGHT / 2,
                    borderWidth: StyleSheet.hairlineWidth,
                    borderColor: edge,
                  },
                ]}
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
