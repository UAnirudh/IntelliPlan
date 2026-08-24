import React from "react";
import { View } from "react-native";
import { useTheme } from "../theme/ThemeProvider";
import { ForecastDay } from "../lib/api";
import { weekdayShort } from "../lib/format";
import { T } from "./ui";
import { radius, space } from "../theme/tokens";

/**
 * Seven days of workload, as a bar per day.
 *
 * Every bar is scaled against the same maximum rather than each against
 * its own available hours — the point of the chart is "Thursday is the
 * heavy one", and per-day scaling makes every day look equally full.
 */
export function ForecastBars({ days }: { days: ForecastDay[] }) {
  const { colors } = useTheme();
  if (!days.length) return null;

  const peak = Math.max(60, ...days.map((d) => d.committed_min + d.prework_min));

  return (
    <View style={{ flexDirection: "row", gap: space.sm, alignItems: "flex-end" }}>
      {days.slice(0, 7).map((d) => {
        const total = d.committed_min + d.prework_min;
        const h = Math.max(4, Math.round((total / peak) * 84));
        // Stress is the server's own judgement of whether the work fits in
        // the hours available, which is not the same as the bar being tall.
        const colour =
          d.stress >= 0.85 ? colors.danger : d.stress >= 0.6 ? colors.warn : colors.accent;
        return (
          <View key={d.date} style={{ flex: 1, alignItems: "center", gap: 6 }}>
            <T variant="micro" tone="muted">
              {total >= 60 ? `${Math.round(total / 60)}h` : total > 0 ? `${total}m` : ""}
            </T>
            <View
              style={{
                width: "100%",
                height: 84,
                justifyContent: "flex-end",
                backgroundColor: colors.bgElevated,
                borderRadius: radius.sm,
                overflow: "hidden",
              }}
            >
              <View style={{ height: h, backgroundColor: colour, borderRadius: radius.sm }} />
            </View>
            <T variant="micro" tone="secondary" weight="600">
              {weekdayShort(d.date).slice(0, 2)}
            </T>
          </View>
        );
      })}
    </View>
  );
}
