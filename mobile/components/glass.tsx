import React from "react";
import {
  AccessibilityInfo,
  ColorValue,
  Platform,
  StyleSheet,
  View,
  ViewProps,
  ViewStyle,
} from "react-native";
import {
  GlassContainer,
  GlassView,
  isGlassEffectAPIAvailable,
  isLiquidGlassAvailable,
} from "expo-glass-effect";
import { useTheme } from "../theme/ThemeProvider";

/**
 * Liquid Glass, in one place.
 *
 * Every glass surface in the app goes through here so the rules live once:
 * native glass only on an iOS build that has the API at runtime (some iOS 26
 * betas shipped without it and crash on first use), and never when the
 * student has Reduce Transparency on — that setting is a request for solid
 * surfaces, and honouring it is the difference between a material and a
 * gimmick. Everywhere else the surface renders exactly as the caller styled
 * it, so Android and web keep the opaque design they already had.
 */

const NATIVE_GLASS = (() => {
  if (Platform.OS !== "ios") return false;
  try {
    return isLiquidGlassAvailable() && isGlassEffectAPIAvailable();
  } catch {
    return false;
  }
})();

let reduceTransparency = false;
const listeners = new Set<(v: boolean) => void>();

if (NATIVE_GLASS) {
  AccessibilityInfo.isReduceTransparencyEnabled()
    .then((v) => {
      reduceTransparency = v;
      listeners.forEach((l) => l(v));
    })
    .catch(() => {});
  AccessibilityInfo.addEventListener("reduceTransparencyChanged", (v) => {
    reduceTransparency = v;
    listeners.forEach((l) => l(v));
  });
}

/** True when surfaces should render as real glass right now. */
export function useGlass(): boolean {
  const [reduced, setReduced] = React.useState(reduceTransparency);
  React.useEffect(() => {
    if (!NATIVE_GLASS) return;
    listeners.add(setReduced);
    return () => {
      listeners.delete(setReduced);
    };
  }, []);
  return NATIVE_GLASS && !reduced;
}

/** The floating glass tab bar's height, and its gap from the screen edges. */
export const FLOATING_TAB_HEIGHT = 64;
export const FLOATING_TAB_INSET = 14;

/** Distance from the bottom of the screen to the capsule's lower edge. */
export function floatingTabBottom(safeBottom: number): number {
  return Math.max(safeBottom - 6, FLOATING_TAB_INSET);
}

/**
 * How much of the bottom of a tab screen the floating bar covers — zero
 * where the bar is docked, since a docked bar already takes its own space.
 */
export function useTabBarOverlap(safeBottom: number): number {
  const glass = useGlass();
  return glass ? floatingTabBottom(safeBottom) + FLOATING_TAB_HEIGHT : 0;
}

/** Hairline that catches light along a glass edge. */
export function useGlassEdge(): string {
  const { scheme } = useTheme();
  return scheme === "dark" ? "rgba(255,255,255,0.14)" : "rgba(255,255,255,0.62)";
}

// Shadow props on a view with a transparent background make iOS cast the
// shadow from every child instead — text grows a drop shadow. Glass brings
// its own depth, so these never reach a GlassView.
const STRIP_ON_GLASS: (keyof ViewStyle)[] = [
  "backgroundColor",
  "shadowColor",
  "shadowOpacity",
  "shadowRadius",
  "shadowOffset",
  "elevation",
];

export type GlassSurfaceProps = ViewProps & {
  /** `clear` is for small controls over busy content; `regular` for panels. */
  variant?: "regular" | "clear";
  /** Lets the glass flex and shimmer under a finger. Use on tappable things. */
  interactive?: boolean;
  /**
   * Colour the glass itself. Only for surfaces whose colour carries meaning
   * — a primary button, a warning — since a neutral tint just greys it out.
   */
  tint?: ColorValue;
  /** Opt one instance out — a cancel link in a row of glass actions. */
  enabled?: boolean;
};

/**
 * A panel that is glass where glass is available and exactly the caller's
 * own style everywhere else. Callers style it as the opaque fallback; the
 * glass path drops the fill and the shadow and keeps the shape.
 */
export function GlassSurface({
  variant = "regular",
  interactive,
  tint,
  enabled = true,
  style,
  children,
  ...rest
}: GlassSurfaceProps) {
  const glass = useGlass() && enabled;
  const { scheme } = useTheme();

  if (!glass) {
    return (
      <View {...rest} style={style}>
        {children}
      </View>
    );
  }

  const flat = { ...(StyleSheet.flatten(style) || {}) } as ViewStyle;
  for (const k of STRIP_ON_GLASS) delete flat[k];

  return (
    <GlassView
      {...rest}
      glassEffectStyle={variant}
      isInteractive={interactive}
      colorScheme={scheme}
      tintColor={tint}
      style={flat}
    >
      {children}
    </GlassView>
  );
}

/**
 * Groups neighbouring glass so it reads as one material: shapes closer than
 * `spacing` melt into each other as they move, the way iOS toolbars do.
 */
export function GlassGroup({
  spacing = 12,
  style,
  children,
  ...rest
}: ViewProps & { spacing?: number }) {
  const glass = useGlass();
  if (!glass) {
    return (
      <View {...rest} style={style}>
        {children}
      </View>
    );
  }
  return (
    <GlassContainer {...rest} spacing={spacing} style={style}>
      {children}
    </GlassContainer>
  );
}

/** A quiet page canvas keeps native and web surfaces visually consistent. */
export function Backdrop() {
  const { colors } = useTheme();
  return <View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: colors.bg }]} />;
}
