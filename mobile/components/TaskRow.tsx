import React from "react";
import { ActivityIndicator, Pressable, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useTheme } from "../theme/ThemeProvider";
import { priorityColour, space } from "../theme/tokens";
import { daysAway, dueLabel, minutes } from "../lib/format";
import { Card, Chip, T } from "./ui";

export type RowTask = {
  title: string;
  course?: string | null;
  due_date?: string | null;
  priority?: string | null;
  est_minutes?: number | null;
  estimated_time?: number | null;
  platform?: string | null;
  source?: string | null;
  why_now?: string | null;
  done?: boolean;
};

/**
 * One assignment, everywhere it appears.
 *
 * Completion is a tap on the circle rather than a swipe: swipe-to-complete
 * competes with the horizontal course filter above the list, and a student
 * clearing six things on a bus should not have to be precise.
 */
export function TaskRow({
  task,
  busy,
  onToggle,
  onPress,
}: {
  task: RowTask;
  busy?: boolean;
  onToggle?: () => void;
  onPress?: () => void;
}) {
  const { colors } = useTheme();
  const days = daysAway(task.due_date);
  const overdue = days !== null && days < 0 && !task.done;
  const est = task.est_minutes ?? task.estimated_time;
  const pri = priorityColour(colors, task.priority);
  const platform = task.platform || task.source;

  return (
    <Card
      style={{
        padding: space.md,
        flexDirection: "row",
        gap: space.md,
        alignItems: "flex-start",
        borderColor: overdue ? colors.borderAccent : colors.border,
        opacity: task.done ? 0.55 : 1,
      }}
    >
      {onToggle ? (
        <Pressable
          onPress={() => {
            Haptics.selectionAsync().catch(() => {});
            onToggle();
          }}
          disabled={busy}
          hitSlop={10}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: !!task.done, busy: !!busy }}
          accessibilityLabel={task.done ? `Restore ${task.title}` : `Mark ${task.title} done`}
          style={{ paddingTop: 2 }}
        >
          {busy ? (
            <ActivityIndicator size="small" color={colors.accent} />
          ) : (
            <Ionicons
              name={task.done ? "checkmark-circle" : "ellipse-outline"}
              size={24}
              color={task.done ? colors.ok : colors.textMuted}
            />
          )}
        </Pressable>
      ) : null}

      <Pressable
        onPress={onPress}
        disabled={!onPress}
        style={{ flex: 1, gap: 6 }}
        accessibilityRole={onPress ? "button" : undefined}
      >
        <T
          variant="base"
          weight="600"
          numberOfLines={2}
          style={task.done ? { textDecorationLine: "line-through" } : undefined}
        >
          {task.title}
        </T>

        {task.why_now ? (
          <T variant="sm" tone="secondary" numberOfLines={2}>
            {task.why_now}
          </T>
        ) : null}

        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 2 }}>
          {task.course ? <Chip label={String(task.course)} /> : null}
          <Chip
            label={dueLabel(task.due_date)}
            icon="time-outline"
            fg={overdue ? colors.dangerText : colors.textMuted}
            bg={overdue ? colors.dangerSoft : undefined}
          />
          {est ? <Chip label={minutes(est)} icon="hourglass-outline" /> : null}
          {task.priority ? (
            <Chip label={String(task.priority)} fg={pri.fg} bg={pri.bg} />
          ) : null}
          {platform ? <Chip label={String(platform)} /> : null}
        </View>
      </Pressable>
    </Card>
  );
}

