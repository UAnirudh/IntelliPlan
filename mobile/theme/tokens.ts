/**
 * Design tokens, ported from static/css/ip-base.css.
 *
 * The web app already owns IntelliPlan's palette, and it is the palette
 * students see every day. Re-deriving one here would mean two sources of
 * truth that drift the first time either side is touched, so these values
 * are copied literally from the `:root[data-theme="light"]` and
 * `[data-theme="dark"]` blocks rather than approximated.
 *
 * React Native has no CSS variables, so the two themes are two objects and
 * a component reads whichever one the provider hands it.
 */

export type Palette = {
  bg: string;
  bgSecondary: string;
  bgCard: string;
  bgElevated: string;
  accent: string;
  accentHover: string;
  accentSoft: string;
  accentMid: string;
  ink: string;
  textPrimary: string;
  textSecondary: string;
  textMuted: string;
  danger: string;
  dangerText: string;
  dangerSoft: string;
  warn: string;
  warnText: string;
  warnSoft: string;
  ok: string;
  okSoft: string;
  border: string;
  borderStrong: string;
  borderAccent: string;
  high: string;
  highBg: string;
  medium: string;
  mediumBg: string;
  low: string;
  lowBg: string;
  navBg: string;
  scrim: string;
  onAccent: string;
};

export const light: Palette = {
  bg: "#f5f4f1",
  bgSecondary: "#fefefe",
  bgCard: "#ffffff",
  bgElevated: "#eceae6",
  accent: "#1a56db",
  accentHover: "#1648b8",
  accentSoft: "rgba(26,86,219,0.06)",
  accentMid: "rgba(26,86,219,0.18)",
  ink: "#1a1a1a",
  textPrimary: "#1a1a1a",
  textSecondary: "#4a4a46",
  textMuted: "#666660",
  danger: "#dc2626",
  dangerText: "#b91c1c",
  dangerSoft: "rgba(220,38,38,0.06)",
  warn: "#c8890f",
  warnText: "#b4780a",
  warnSoft: "rgba(200,137,15,0.08)",
  ok: "#0a7558",
  okSoft: "rgba(10,117,88,0.08)",
  border: "rgba(0,0,0,0.08)",
  borderStrong: "rgba(0,0,0,0.11)",
  borderAccent: "rgba(26,86,219,0.24)",
  high: "#c62828",
  highBg: "rgba(198,40,40,0.07)",
  medium: "#c27a06",
  mediumBg: "rgba(194,122,6,0.07)",
  low: "#0a7558",
  lowBg: "rgba(10,117,88,0.07)",
  navBg: "rgba(254,254,254,0.94)",
  scrim: "rgba(18,18,23,0.44)",
  onAccent: "#ffffff",
};

export const dark: Palette = {
  bg: "#101012",
  bgSecondary: "#17171a",
  bgCard: "#1c1c20",
  bgElevated: "#242428",
  accent: "#5b93f5",
  accentHover: "#7aabf8",
  accentSoft: "rgba(91,147,245,0.10)",
  accentMid: "rgba(91,147,245,0.22)",
  ink: "#e6e6e2",
  textPrimary: "#e6e6e2",
  textSecondary: "#9e9e9a",
  textMuted: "#878780",
  danger: "#f87171",
  dangerText: "#fca5a5",
  dangerSoft: "rgba(248,113,113,0.10)",
  warn: "#e0a94a",
  warnText: "#f0c477",
  warnSoft: "rgba(224,169,74,0.10)",
  ok: "#3dd4a0",
  okSoft: "rgba(61,212,160,0.10)",
  border: "rgba(255,255,255,0.08)",
  borderStrong: "rgba(255,255,255,0.14)",
  borderAccent: "rgba(91,147,245,0.26)",
  high: "#f07070",
  highBg: "rgba(240,112,112,0.12)",
  medium: "#f0b429",
  mediumBg: "rgba(240,180,41,0.12)",
  low: "#3dd4a0",
  lowBg: "rgba(61,212,160,0.12)",
  navBg: "rgba(16,16,18,0.94)",
  scrim: "rgba(0,0,0,0.62)",
  onAccent: "#0d1220",
};

/** 4px base, matching --space-1..8 on the web. */
export const space = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
} as const;

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 22,
  pill: 999,
} as const;

/**
 * The web ramp is fluid (clamp against viewport width). A phone has one
 * viewport, so each step collapses to the value the clamp resolves to at
 * ~390pt — the size the design was actually checked at.
 */
export const type = {
  micro: { fontSize: 11, letterSpacing: 0.8 },
  xs: { fontSize: 12.5, letterSpacing: 0 },
  sm: { fontSize: 13.5, letterSpacing: 0 },
  base: { fontSize: 15.5, letterSpacing: -0.09 },
  md: { fontSize: 17.5, letterSpacing: -0.19 },
  lg: { fontSize: 21, letterSpacing: -0.34 },
  xl: { fontSize: 26, letterSpacing: -0.57 },
  xxl: { fontSize: 32, letterSpacing: -0.9 },
} as const;

/** Priority colour for a tier name, whatever casing the server used. */
export function priorityColour(p: Palette, tier?: string | null) {
  switch (String(tier || "").toLowerCase()) {
    case "high":
    case "critical":
    case "urgent":
      return { fg: p.high, bg: p.highBg };
    case "low":
      return { fg: p.low, bg: p.lowBg };
    case "medium":
    case "normal":
      return { fg: p.medium, bg: p.mediumBg };
    default:
      return { fg: p.textMuted, bg: p.bgElevated };
  }
}
