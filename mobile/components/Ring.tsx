import React from "react";
import { View } from "react-native";
import Svg, { Circle } from "react-native-svg";
import { useTheme } from "../theme/ThemeProvider";
import { T } from "./ui";

/**
 * The Academic Health Score, as a dial.
 *
 * Colour comes from the tier the server assigned, not from the number:
 * the server knows what counts as "watch" for this student, and picking a
 * threshold here would make the app disagree with the website about the
 * same score.
 */
export function Ring({
  score,
  tier,
  size = 108,
}: {
  score: number;
  tier?: string;
  size?: number;
}) {
  const { colors } = useTheme();
  const stroke = 9;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score)) / 100;

  const colour =
    {
      thriving: colors.ok,
      steady: colors.accent,
      watch: colors.warn,
      at_risk: colors.danger,
    }[String(tier || "").toLowerCase()] ?? colors.accent;

  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <Svg width={size} height={size} style={{ position: "absolute" }}>
        <Circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={colors.bgElevated}
          strokeWidth={stroke}
          fill="none"
        />
        <Circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={colour}
          strokeWidth={stroke}
          strokeLinecap="round"
          fill="none"
          strokeDasharray={`${c} ${c}`}
          strokeDashoffset={c * (1 - pct)}
          // Without this the arc starts at 3 o'clock, which reads as the
          // dial already being a quarter full at zero.
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </Svg>
      <T variant="xl" weight="700" style={{ color: colour }}>
        {Math.round(score)}
      </T>
      {/* The day-over-day delta used to sit here and did not fit: at 108pt
          the ring is narrower than "−3 vs yesterday", so the caption
          overflowed the dial and clipped its own minus sign. It reads as a
          chip beside the tier instead, where it has the width to say what
          it means. */}
      <T variant="micro" tone="muted">
        out of 100
      </T>
    </View>
  );
}
