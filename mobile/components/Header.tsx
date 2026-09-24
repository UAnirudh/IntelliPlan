import React from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";
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
  showSettings = true,
}: {
  title: string;
  subtitle?: string | null;
  right?: React.ReactNode;
  showSettings?: boolean;
}) {
  const { colors } = useTheme();
  const glass = useGlass();
  const edge = useGlassEdge();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  return (
    <GlassSurface
      style={{
        paddingTop: insets.top + space.sm,
        paddingBottom: space.md,
        paddingHorizontal: space.lg,
        backgroundColor: colors.bg,
        borderBottomWidth: glass ? StyleSheet.hairlineWidth : 1,
        borderBottomColor: glass ? edge : colors.border,
        flexDirection: "row",
        alignItems: "center",
        gap: space.md,
      }}
    >
      <View style={{ flex: 1 }}>
        <T variant="lg" weight="700" numberOfLines={1}>
          {title}
        </T>
        {subtitle ? (
          <T variant="sm" tone="muted" numberOfLines={1}>
            {subtitle}
          </T>
        ) : null}
      </View>

      {/* One container for every action, so adjacent glass buttons merge
          at their edges like an iOS toolbar cluster. */}
      <GlassGroup spacing={space.sm} style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
        {right}

        {showSettings ? (
          <IconButton
            icon="person-circle-outline"
            size={26}
            label="Settings"
            onPress={() => router.push("/settings")}
          />
        ) : null}
      </GlassGroup>
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
      hitSlop={glass ? 6 : 10}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => ({ opacity: pressed && !glass ? 0.6 : 1 })}
    >
      {glass ? (
        <GlassSurface
          interactive
          style={{
            width: 38,
            height: 38,
            borderRadius: 19,
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
