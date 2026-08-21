import React from "react";
import {
  ActivityIndicator,
  Platform,
  Pressable,
  PressableProps,
  ScrollView,
  StyleProp,
  Text,
  TextInput,
  TextInputProps,
  TextProps,
  TextStyle,
  View,
  ViewProps,
  ViewStyle,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space, type as typeScale } from "../theme/tokens";

/* ── Text ─────────────────────────────────────────────────────────── */

type Variant = keyof typeof typeScale;
type Tone = "primary" | "secondary" | "muted" | "accent" | "danger" | "warn" | "ok" | "onAccent";

export function T({
  variant = "base",
  tone = "primary",
  weight,
  style,
  ...rest
}: TextProps & { variant?: Variant; tone?: Tone; weight?: TextStyle["fontWeight"] }) {
  const { colors } = useTheme();
  const toneColor = {
    primary: colors.textPrimary,
    secondary: colors.textSecondary,
    muted: colors.textMuted,
    accent: colors.accent,
    danger: colors.dangerText,
    warn: colors.warnText,
    ok: colors.ok,
    onAccent: colors.onAccent,
  }[tone];

  return (
    <Text
      {...rest}
      style={[
        typeScale[variant],
        { color: toneColor, fontWeight: weight ?? "400" },
        // Body copy needs leading; a one-line label does not, and forcing
        // it there pushes single lines off-centre inside pills and rows.
        variant === "base" || variant === "sm" ? { lineHeight: typeScale[variant].fontSize * 1.5 } : null,
        style,
      ]}
    />
  );
}

/** Uppercase section label, matching --tr-label on the web. */
export function Label({ style, ...rest }: TextProps) {
  return (
    <T
      variant="micro"
      tone="muted"
      weight="700"
      {...rest}
      style={[{ textTransform: "uppercase" }, style]}
    />
  );
}

/* ── Surfaces ─────────────────────────────────────────────────────── */

export function Card({ style, children, ...rest }: ViewProps) {
  const { colors, scheme } = useTheme();
  return (
    <View
      {...rest}
      style={[
        {
          backgroundColor: colors.bgCard,
          borderRadius: radius.xl,
          borderWidth: 1,
          borderColor: colors.border,
          padding: space.lg,
          // Android renders `elevation` and ignores shadow*; iOS the
          // reverse. Setting both keeps one card looking like one card.
          ...Platform.select({
            ios: {
              shadowColor: "#1c1914",
              shadowOpacity: scheme === "dark" ? 0.5 : 0.06,
              shadowRadius: 20,
              shadowOffset: { width: 0, height: 6 },
            },
            android: { elevation: scheme === "dark" ? 0 : 2 },
            default: {},
          }),
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}

/** Page background + safe horizontal padding. */
export function Screen({ style, children, ...rest }: ViewProps) {
  const { colors } = useTheme();
  return (
    <View {...rest} style={[{ flex: 1, backgroundColor: colors.bg }, style]}>
      {children}
    </View>
  );
}

export function Divider() {
  const { colors } = useTheme();
  return <View style={{ height: 1, backgroundColor: colors.border, marginVertical: space.md }} />;
}

/* ── Button ───────────────────────────────────────────────────────── */

type BtnKind = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  title,
  kind = "primary",
  busy,
  icon,
  style,
  disabled,
  ...rest
}: PressableProps & {
  title: string;
  kind?: BtnKind;
  busy?: boolean;
  icon?: keyof typeof Ionicons.glyphMap;
  style?: StyleProp<ViewStyle>;
}) {
  const { colors } = useTheme();
  const off = disabled || busy;

  const skin: Record<BtnKind, { bg: string; fg: string; border: string }> = {
    primary: { bg: colors.accent, fg: colors.onAccent, border: colors.accent },
    secondary: { bg: colors.bgCard, fg: colors.textPrimary, border: colors.borderStrong },
    ghost: { bg: "transparent", fg: colors.accent, border: "transparent" },
    danger: { bg: colors.dangerSoft, fg: colors.dangerText, border: colors.danger },
  };
  const s = skin[kind];

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !!off, busy: !!busy }}
      disabled={off}
      {...rest}
      style={({ pressed }) => [
        {
          backgroundColor: s.bg,
          borderColor: s.border,
          borderWidth: kind === "ghost" ? 0 : 1,
          borderRadius: radius.pill,
          paddingVertical: 13,
          paddingHorizontal: space.xl,
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "center",
          gap: space.sm,
          opacity: off ? 0.55 : pressed ? 0.82 : 1,
        },
        style as ViewStyle,
      ]}
    >
      {busy ? (
        <ActivityIndicator color={s.fg} size="small" />
      ) : (
        <>
          {icon ? <Ionicons name={icon} size={17} color={s.fg} /> : null}
          <Text style={{ color: s.fg, fontWeight: "600", fontSize: 15.5 }}>{title}</Text>
        </>
      )}
    </Pressable>
  );
}

/* ── Chip / pill ──────────────────────────────────────────────────── */

export function Chip({
  label,
  fg,
  bg,
  icon,
  style,
}: {
  label: string;
  fg?: string;
  bg?: string;
  icon?: keyof typeof Ionicons.glyphMap;
  style?: StyleProp<ViewStyle>;
}) {
  const { colors } = useTheme();
  return (
    <View
      style={[
        {
          backgroundColor: bg ?? colors.bgElevated,
          borderRadius: radius.pill,
          paddingHorizontal: 10,
          paddingVertical: 4,
          flexDirection: "row",
          alignItems: "center",
          gap: 4,
          alignSelf: "flex-start",
        },
        style,
      ]}
    >
      {icon ? <Ionicons name={icon} size={11} color={fg ?? colors.textMuted} /> : null}
      <Text style={{ color: fg ?? colors.textMuted, fontSize: 11.5, fontWeight: "600" }}>
        {label}
      </Text>
    </View>
  );
}

/** Horizontally scrolling single-select, used for filters and options. */
export function SegmentedRow<V extends string | number>({
  options,
  value,
  onChange,
}: {
  options: { label: string; value: V }[];
  value: V;
  onChange: (v: V) => void;
}) {
  const { colors } = useTheme();
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={{ gap: space.sm, paddingRight: space.lg }}
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <Pressable
            key={String(o.value)}
            onPress={() => onChange(o.value)}
            accessibilityRole="button"
            accessibilityState={{ selected: on }}
            style={{
              paddingHorizontal: 14,
              paddingVertical: 8,
              borderRadius: radius.pill,
              backgroundColor: on ? colors.accent : colors.bgCard,
              borderWidth: 1,
              borderColor: on ? colors.accent : colors.border,
            }}
          >
            <Text
              style={{
                color: on ? colors.onAccent : colors.textSecondary,
                fontWeight: "600",
                fontSize: 13.5,
              }}
            >
              {o.label}
            </Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

/* ── Input ────────────────────────────────────────────────────────── */

export function Field({
  label,
  style,
  ...rest
}: TextInputProps & { label?: string; style?: StyleProp<ViewStyle> }) {
  const { colors } = useTheme();
  return (
    <View style={[{ gap: 6 }, style]}>
      {label ? <Label>{label}</Label> : null}
      <TextInput
        placeholderTextColor={colors.textMuted}
        {...rest}
        style={[
          {
            borderWidth: 1,
            borderColor: colors.border,
            backgroundColor: colors.bgSecondary,
            borderRadius: radius.md,
            paddingHorizontal: 14,
            paddingVertical: Platform.OS === "ios" ? 13 : 10,
            fontSize: 16,
            color: colors.textPrimary,
          },
          rest.multiline ? { minHeight: 92, textAlignVertical: "top" } : null,
        ]}
      />
    </View>
  );
}

/* ── States ───────────────────────────────────────────────────────── */

export function Loading({ label }: { label?: string }) {
  const { colors } = useTheme();
  return (
    <View style={{ flex: 1, alignItems: "center", justifyContent: "center", gap: space.md }}>
      <ActivityIndicator color={colors.accent} />
      {label ? <T tone="muted" variant="sm">{label}</T> : null}
    </View>
  );
}

export function EmptyState({
  icon = "sparkles-outline",
  title,
  body,
  action,
}: {
  icon?: keyof typeof Ionicons.glyphMap;
  title: string;
  body?: string;
  action?: React.ReactNode;
}) {
  const { colors } = useTheme();
  return (
    <View style={{ alignItems: "center", padding: space.xxl, gap: space.md }}>
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
        <Ionicons name={icon} size={26} color={colors.accent} />
      </View>
      <T variant="md" weight="600" style={{ textAlign: "center" }}>
        {title}
      </T>
      {body ? (
        <T tone="muted" variant="sm" style={{ textAlign: "center", maxWidth: 300 }}>
          {body}
        </T>
      ) : null}
      {action}
    </View>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <View style={{ padding: space.xl, gap: space.lg, alignItems: "center" }}>
      <EmptyState icon="cloud-offline-outline" title="That didn't load" body={message} />
      {onRetry ? <Button title="Try again" kind="secondary" icon="refresh" onPress={onRetry} /> : null}
    </View>
  );
}

/**
 * A quiet strip above content, for a truth the student should see but that
 * is not an error — stale cached data, a feature their school has not
 * switched on. Deliberately not an Alert: those interrupt.
 */
export function Notice({
  text,
  tone = "warn",
  icon = "information-circle-outline",
}: {
  text: string;
  tone?: "warn" | "accent";
  icon?: keyof typeof Ionicons.glyphMap;
}) {
  const { colors } = useTheme();
  const fg = tone === "warn" ? colors.warnText : colors.accent;
  const bg = tone === "warn" ? colors.warnSoft : colors.accentSoft;
  return (
    <View
      style={{
        flexDirection: "row",
        gap: space.sm,
        alignItems: "center",
        backgroundColor: bg,
        borderRadius: radius.md,
        paddingHorizontal: space.md,
        paddingVertical: space.sm,
      }}
    >
      <Ionicons name={icon} size={15} color={fg} />
      <Text style={{ color: fg, fontSize: 13, flex: 1 }}>{text}</Text>
    </View>
  );
}

export { space, radius };
