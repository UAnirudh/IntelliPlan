import React from "react";
import {
  ActivityIndicator,
  Platform,
  Pressable,
  PressableProps,
  ScrollView,
  StyleProp,
  StyleSheet,
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
import { Backdrop, GlassGroup, GlassSurface, useGlass, useGlassEdge } from "./glass";
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

export { GlassGroup, GlassSurface, useGlass } from "./glass";

export function Card({ style, children, ...rest }: ViewProps) {
  const { colors, scheme } = useTheme();
  const glass = useGlass();
  const edge = useGlassEdge();
  return (
    <GlassSurface
      {...rest}
      style={[
        {
          backgroundColor: colors.bgCard,
          borderRadius: radius.xl,
          borderWidth: glass ? StyleSheet.hairlineWidth : 1,
          borderColor: glass ? edge : colors.border,
          padding: space.lg,
          // Android renders `elevation` and ignores shadow*; iOS the
          // reverse. Setting both keeps one card looking like one card.
          // GlassSurface drops both on the glass path.
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
    </GlassSurface>
  );
}

/** Page background + safe horizontal padding. */
export function Screen({ style, children, ...rest }: ViewProps) {
  const { colors } = useTheme();
  return (
    <View {...rest} style={[{ flex: 1, backgroundColor: colors.bg }, style]}>
      <Backdrop />
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
  const glass = useGlass();
  const edge = useGlassEdge();
  const off = disabled || busy;

  const skin: Record<BtnKind, { bg: string; fg: string; border: string; tint?: string }> = {
    primary: { bg: colors.accent, fg: colors.onAccent, border: colors.accent, tint: colors.accent },
    secondary: { bg: colors.bgCard, fg: colors.textPrimary, border: colors.borderStrong },
    ghost: { bg: "transparent", fg: colors.accent, border: "transparent" },
    danger: { bg: colors.dangerSoft, fg: colors.dangerText, border: colors.danger, tint: colors.dangerSoft },
  };
  const s = skin[kind];
  // A ghost button is a line of text, not a surface; glass would give it a body.
  const onGlass = glass && kind !== "ghost";

  const content = busy ? (
    <ActivityIndicator color={s.fg} size="small" />
  ) : (
    <>
      {icon ? <Ionicons name={icon} size={17} color={s.fg} /> : null}
      <Text style={{ color: s.fg, fontWeight: "600", fontSize: 15.5 }}>{title}</Text>
    </>
  );

  const body: ViewStyle = {
    backgroundColor: s.bg,
    borderColor: onGlass ? edge : s.border,
    borderWidth: kind === "ghost" ? 0 : onGlass ? StyleSheet.hairlineWidth : 1,
    borderRadius: radius.pill,
    paddingVertical: 13,
    paddingHorizontal: space.xl,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: space.sm,
  };

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !!off, busy: !!busy }}
      disabled={off}
      {...rest}
      style={({ pressed }) => [
        onGlass ? { borderRadius: radius.pill } : body,
        // Native glass answers the touch itself; dimming on top of that
        // reads as lag, so only the fallback fades while pressed.
        { opacity: off ? 0.55 : pressed && !onGlass ? 0.82 : 1 },
        style as ViewStyle,
      ]}
    >
      {onGlass ? (
        <GlassSurface interactive tint={s.tint} style={[body, { flexGrow: 1 }]}>
          {content}
        </GlassSurface>
      ) : (
        content
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
    <GlassSurface
      variant="clear"
      tint={bg}
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
    </GlassSurface>
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
  const glass = useGlass();
  const edge = useGlassEdge();
  const scroller = React.useRef<ScrollView>(null);
  // Each chip's x-offset and width, filled in as they lay out. Needed
  // because a row can open with a selection that is already off-screen —
  // a task estimated at 90 minutes preselects the last chip — and a
  // selected option the student cannot see reads as nothing being chosen.
  const spans = React.useRef<Record<string, { x: number; w: number }>>({});
  const revealed = React.useRef(false);

  const reveal = React.useCallback(() => {
    if (revealed.current) return;
    const span = spans.current[String(value)];
    if (!span) return;
    revealed.current = true;
    // Left-aligned rather than centred: the chips before it are context,
    // and scrolling further than necessary loses them.
    if (span.x > 0) scroller.current?.scrollTo({ x: Math.max(0, span.x - space.lg), animated: false });
  }, [value]);

  return (
    <ScrollView
      ref={scroller}
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={{ paddingRight: space.lg }}
    >
      {/* Grouped so the selection's glass flows between neighbours as it
          moves, instead of each chip being its own island. */}
      <GlassGroup spacing={space.sm} style={{ flexDirection: "row", gap: space.sm }}>
        {options.map((o) => {
          const on = o.value === value;
          return (
            <Pressable
              key={String(o.value)}
              onPress={() => onChange(o.value)}
              onLayout={(e) => {
                spans.current[String(o.value)] = {
                  x: e.nativeEvent.layout.x,
                  w: e.nativeEvent.layout.width,
                };
                if (on) reveal();
              }}
              accessibilityRole="button"
              accessibilityState={{ selected: on }}
            >
              <GlassSurface
                interactive
                variant={on ? "regular" : "clear"}
                tint={on ? colors.accent : undefined}
                style={{
                  paddingHorizontal: 14,
                  paddingVertical: 8,
                  borderRadius: radius.pill,
                  backgroundColor: on ? colors.accent : colors.bgCard,
                  borderWidth: glass ? StyleSheet.hairlineWidth : 1,
                  borderColor: glass ? edge : on ? colors.accent : colors.border,
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
              </GlassSurface>
            </Pressable>
          );
        })}
      </GlassGroup>
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
  const glass = useGlass();
  const edge = useGlassEdge();
  return (
    <View style={[{ gap: 6 }, style]}>
      {label ? <Label>{label}</Label> : null}
      <GlassSurface
        style={{
          borderWidth: glass ? StyleSheet.hairlineWidth : 1,
          borderColor: glass ? edge : colors.border,
          backgroundColor: colors.bgSecondary,
          borderRadius: radius.md,
        }}
      >
        <TextInput
          placeholderTextColor={colors.textMuted}
          {...rest}
          style={[
            {
              paddingHorizontal: 14,
              paddingVertical: Platform.OS === "ios" ? 13 : 10,
              fontSize: 16,
              color: colors.textPrimary,
            },
            rest.multiline ? { minHeight: 92, textAlignVertical: "top" } : null,
          ]}
        />
      </GlassSurface>
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
      <GlassSurface
        tint={colors.accentSoft}
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
      </GlassSurface>
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
    <GlassSurface
      tint={bg}
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
    </GlassSurface>
  );
}

export { space, radius };
