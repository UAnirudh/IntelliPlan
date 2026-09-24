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
import { LinearGradient } from "expo-linear-gradient";
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

/**
 * The light behind the glass.
 *
 * Glass over a flat fill has nothing to bend, so it reads as a slightly
 * grey box. Three soft washes — the brand blue up top, a thread of green
 * across the middle, something warm low down — give every surface light to
 * refract, while staying quiet enough that the page still reads as the same
 * off-white (or near-black) it always was.
 */
export function Backdrop() {
  const { colors, scheme } = useTheme();
  const dark = scheme === "dark";
  // Each wash fades to its own colour at zero alpha. Fading to the keyword
  // `transparent` means fading through transparent *black*, which leaves a
  // grey band across the middle of a light page.
  const cool = dark ? [91, 147, 245, 0.2] : [26, 86, 219, 0.13];
  const mint = dark ? [61, 212, 160, 0.08] : [10, 117, 88, 0.07];
  const warm = dark ? [224, 169, 74, 0.1] : [200, 137, 15, 0.1];

  return (
    <View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: colors.bg }]}>
      <LinearGradient
        colors={[rgba(cool), rgba(cool, 0)]}
        start={{ x: 0, y: 0 }}
        end={{ x: 0.85, y: 0.55 }}
        style={StyleSheet.absoluteFill}
      />
      <LinearGradient
        colors={[rgba(mint, 0), rgba(mint), rgba(mint, 0)]}
        locations={[0.25, 0.55, 0.85]}
        start={{ x: 1, y: 0.1 }}
        end={{ x: 0, y: 0.9 }}
        style={StyleSheet.absoluteFill}
      />
      <LinearGradient
        colors={[rgba(warm, 0), rgba(warm)]}
        start={{ x: 0.2, y: 0.45 }}
        end={{ x: 1, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
    </View>
  );
}

function rgba([r, g, b, a]: number[], alpha = a): string {
  return `rgba(${r},${g},${b},${alpha})`;
}
