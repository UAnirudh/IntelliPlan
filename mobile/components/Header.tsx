import React from "react";
import { Platform, Pressable, StyleSheet, View } from "react-native";
import { useRouter, useSegments } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useTheme } from "../theme/ThemeProvider";
import { space } from "../theme/tokens";
import { GlassGroup, GlassSurface, useGlass, useGlassEdge } from "./glass";
import { T } from "./ui";

/**
 * The bar at the top of every tab.
 *
 * Hand-rolled rather than the navigator's own header so the title, the
 * subtitle and the actions all sit on the same type ramp as the screen
 * beneath them — a stock header would be the one piece of chrome the
 * design system does not reach.
 */
export function Header({
  title,
  subtitle,
  right,
  showSettings = false,
  showBack = false,
  onBack,
}: {
  title: string;
  subtitle?: string | null;
  right?: React.ReactNode;
  showSettings?: boolean;
  showBack?: boolean;
  onBack?: () => void;
}) {
  const { colors } = useTheme();
  const glass = useGlass();
  const edge = useGlassEdge();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const segments = useSegments();
  const isMoreScreen = segments.includes("more");
  const goBack = () => {
    if (onBack) {
      onBack();
      return;
    }
    if (router.canGoBack()) router.back();
    else router.replace("/more");
  };

  return (
    <GlassSurface
      style={{
        paddingTop: insets.top + space.sm,
        paddingBottom: space.md,
        paddingHorizontal: space.lg,
        backgroundColor: colors.bg,
        borderBottomWidth: glass ? StyleSheet.hairlineWidth : 1,
        borderBottomColor: glass ? edge : colors.border,
        gap: space.xs,
      }}
    >
      <View style={{ minHeight: 42, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: space.xs }}>
          {showBack ? <IconButton icon="chevron-back" size={22} label="Back to previous screen" onPress={goBack} /> : null}
          <View accessibilityLabel="IntelliPlan" accessibilityRole="header" style={{ flexDirection: "row", alignItems: "baseline" }}>
            <T variant="md" weight="800">Intelli</T>
            <T variant="md" weight="800" tone="accent">Plan</T>
          </View>
        </View>
        {/* Keep the page actions together at the top edge like a native
            toolbar; this also leaves the page title room on narrow phones. */}
        <GlassGroup spacing={space.sm} style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
          {right}
          {Platform.OS !== "web" && !isMoreScreen ? (
            <IconButton
              icon="ellipsis-horizontal"
              size={24}
              label="More navigation options"
              onPress={() => router.push("/more")}
            />
          ) : null}
          {showSettings ? (
            <IconButton
              icon="person-circle-outline"
              size={26}
              label="Settings"
              onPress={() => router.push("/settings")}
            />
          ) : null}
        </GlassGroup>
      </View>

      <View style={{ gap: 1 }}>
        <T variant="xl" weight="700" numberOfLines={1}>
          {title}
        </T>
        {subtitle ? (
          <T variant="sm" tone="secondary" numberOfLines={1}>
            {subtitle}
          </T>
        ) : null}
      </View>
    </GlassSurface>
  );
}

/** Round icon button, for header actions. A glass bubble where glass exists. */
export function IconButton({
  icon,
  onPress,
  label,
  tint,
  size = 22,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  onPress: () => void;
  label: string;
  tint?: string;
  size?: number;
}) {
  const { colors } = useTheme();
  const glass = useGlass();
  const edge = useGlassEdge();
  return (
    <Pressable
      onPress={onPress}
      hitSlop={glass ? 2 : 4}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => ({
        width: 44,
        height: 44,
        borderRadius: 22,
        alignItems: "center",
        justifyContent: "center",
        opacity: pressed && !glass ? 0.6 : 1,
      })}
    >
      {glass ? (
        <GlassSurface
          interactive
          style={{
            width: 44,
            height: 44,
            borderRadius: 22,
            borderWidth: StyleSheet.hairlineWidth,
            borderColor: edge,
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Ionicons name={icon} size={size - 2} color={tint ?? colors.textPrimary} />
        </GlassSurface>
      ) : (
        <Ionicons name={icon} size={size + 2} color={tint ?? colors.textSecondary} />
      )}
    </Pressable>
  );
}
