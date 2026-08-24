import React from "react";
import { Pressable, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useTheme } from "../theme/ThemeProvider";
import { space } from "../theme/tokens";
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
  const insets = useSafeAreaInsets();
  const router = useRouter();

  return (
    <View
      style={{
        paddingTop: insets.top + space.sm,
        paddingBottom: space.md,
        paddingHorizontal: space.lg,
        backgroundColor: colors.bg,
        borderBottomWidth: 1,
        borderBottomColor: colors.border,
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

      {right}

      {showSettings ? (
        <Pressable
          onPress={() => router.push("/settings")}
          hitSlop={10}
          accessibilityRole="button"
          accessibilityLabel="Settings"
        >
          <Ionicons name="person-circle-outline" size={28} color={colors.textSecondary} />
        </Pressable>
      ) : null}
    </View>
  );
}

/** Round icon button, for header actions. */
export function IconButton({
  icon,
  onPress,
  label,
  tint,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  onPress: () => void;
  label: string;
  tint?: string;
}) {
  const { colors } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      hitSlop={10}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => ({ opacity: pressed ? 0.6 : 1 })}
    >
      <Ionicons name={icon} size={24} color={tint ?? colors.textSecondary} />
    </Pressable>
  );
}
