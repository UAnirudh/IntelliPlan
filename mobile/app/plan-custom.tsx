import React, { useCallback, useMemo, useState } from "react";
import { Pressable, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import {
  buildManualPlan,
  deletePreset,
  getPresets,
  ManualBlock,
  ManualPreset,
  savePreset,
} from "../lib/api";
import { useQuery } from "../lib/useQuery";
import { useConfirm } from "../components/Confirm";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, Field, Label, Loading, Notice, Screen, T } from "../components/ui";

/**
 * Placing your own blocks, instead of letting the generator do it.
 *
 * The generated plan answers "fit my work into my free time". This
 * answers a different question — "here is when I actually study, work
 * around it" — and a student who has swimming on Tuesdays and a shift on
 * Saturday cannot express that by turning a hours-per-day dial.
 *
 * A routine is saved once and applied to whichever days it fits, because
 * the real shape of a week is one or two routines repeated, not seven
 * bespoke days.
 */

/** Half-hour slots from 06:00 to 23:30. */
const SLOTS: string[] = (() => {
  const out: string[] = [];
  for (let h = 6; h <= 23; h++) {
    out.push(`${String(h).padStart(2, "0")}:00`);
    out.push(`${String(h).padStart(2, "0")}:30`);
  }
  return out;
})();

function label12(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h >= 12 ? "pm" : "am";
  const hour = h % 12 === 0 ? 12 : h % 12;
  return m === 0 ? `${hour}${suffix}` : `${hour}:${String(m).padStart(2, "0")}${suffix}`;
}

function minutesOf(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

/** The next seven days, as {iso, label}. */
function nextWeek(): { iso: string; short: string; day: string }[] {
  const out = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date();
    d.setDate(d.getDate() + i);
    out.push({
      iso: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`,
      short: d.toLocaleDateString(undefined, { weekday: "short" }),
      day: String(d.getDate()),
    });
  }
  return out;
}

export default function PlanCustomScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const confirm = useConfirm();

  const presets = useQuery<ManualPreset[]>("presets", getPresets);

  const [blocks, setBlocks] = useState<ManualBlock[]>([
    { start: "16:00", end: "17:00", label: "Study" },
  ]);
  const [routineName, setRoutineName] = useState("");
  const [days, setDays] = useState<Set<string>>(() => new Set([nextWeek()[0].iso]));
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const week = useMemo(nextWeek, []);

  const addBlock = useCallback(() => {
    setBlocks((prev) => {
      // A new block starts where the last one ended, which is what someone
      // laying out an evening actually wants — not another 4pm block
      // overlapping the one they just made.
      const last = prev[prev.length - 1];
      const startIdx = last ? Math.min(SLOTS.indexOf(last.end), SLOTS.length - 2) : 0;
      return [
        ...prev,
        {
          start: SLOTS[Math.max(0, startIdx)],
          end: SLOTS[Math.min(SLOTS.length - 1, Math.max(0, startIdx) + 2)],
          label: "Study",
        },
      ];
    });
  }, []);

  const setBlock = useCallback((i: number, patch: Partial<ManualBlock>) => {
    setBlocks((prev) =>
      prev.map((b, j) => {
        if (j !== i) return b;
        const next = { ...b, ...patch };
        // An end before its start is not a block; nudge it to the next
        // slot rather than refusing the tap and leaving them stuck.
        if (minutesOf(next.end) <= minutesOf(next.start)) {
          const si = SLOTS.indexOf(next.start);
          next.end = SLOTS[Math.min(SLOTS.length - 1, si + 1)];
        }
        return next;
      }),
    );
  }, []);

  const removeBlock = useCallback((i: number) => {
    setBlocks((prev) => (prev.length <= 1 ? prev : prev.filter((_, j) => j !== i)));
  }, []);

  const applyPreset = useCallback((p: ManualPreset) => {
    setBlocks(p.blocks?.length ? p.blocks : blocks);
    setRoutineName(p.name);
    Haptics.selectionAsync().catch(() => {});
  }, [blocks]);

  const removePreset = useCallback(
    async (p: ManualPreset) => {
      const choice = await confirm({
        title: `Delete "${p.name}"?`,
        message: "Plans you already built from it stay as they are.",
        actions: [
          { label: "Delete", destructive: true },
          { label: "Cancel", cancel: true },
        ],
      });
      if (choice !== 0) return;
      try {
        await deletePreset(p.id);
        await presets.refresh();
      } catch (e: any) {
        setNote(e?.message || "That didn't delete.");
      }
    },
    [confirm, presets],
  );

  const saveRoutine = useCallback(async () => {
    const name = routineName.trim();
    if (!name) {
      setNote("Give the routine a name so you can find it again.");
      return;
    }
    setBusy("save");
    setNote(null);
    try {
      await savePreset(name, blocks);
      await presets.refresh();
      setNote(`Saved "${name}".`);
    } catch (e: any) {
      setNote(e?.message || "Couldn't save that routine.");
    } finally {
      setBusy(null);
    }
  }, [routineName, blocks, presets]);

  const build = useCallback(async () => {
    if (!days.size) {
      setNote("Pick at least one day.");
      return;
    }
    setBusy("build");
    setNote(null);
    try {
      await buildManualPlan({
        days: Array.from(days).sort().map((iso) => ({ date: iso, blocks })),
        name: routineName.trim() || undefined,
      });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      router.back();
    } catch (e: any) {
      setNote(e?.message || "Couldn't build that plan.");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setBusy(null);
    }
  }, [days, blocks, routineName, router]);

  const totalMinutes = blocks.reduce(
    (n, b) => n + Math.max(0, minutesOf(b.end) - minutesOf(b.start)),
    0,
  );

  return (
    <Screen>
      <View
        style={{
          flexDirection: "row",
          alignItems: "center",
          paddingTop: insets.top + space.sm,
          paddingHorizontal: space.lg,
          paddingBottom: space.md,
          borderBottomWidth: 1,
          borderBottomColor: colors.border,
        }}
      >
        <View style={{ flex: 1 }}>
          <T variant="lg" weight="700">
            Your own hours
          </T>
          <T variant="sm" tone="muted">
            {blocks.length} block{blocks.length === 1 ? "" : "s"} ·{" "}
            {Math.floor(totalMinutes / 60)}h {totalMinutes % 60}m · {days.size} day
            {days.size === 1 ? "" : "s"}
          </T>
        </View>
        <Pressable onPress={() => router.back()} hitSlop={10} accessibilityRole="button" accessibilityLabel="Close">
          <Ionicons name="close" size={26} color={colors.textSecondary} />
        </Pressable>
      </View>

      <ScrollView
        contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: insets.bottom + 40 }}
        keyboardShouldPersistTaps="handled"
      >
        {/* ── Saved routines ── */}
        {presets.loading ? (
          <Loading label="Loading your routines…" />
        ) : presets.data?.length ? (
          <Card style={{ gap: space.sm }}>
            <Label>Saved routines</Label>
            <T variant="xs" tone="muted">
              Tap one to load it, then pick which days it applies to.
            </T>
            {presets.data.map((p) => (
              <Pressable key={p.id} onPress={() => applyPreset(p)} accessibilityRole="button">
                <View
                  style={{
                    flexDirection: "row",
                    alignItems: "center",
                    gap: space.sm,
                    paddingVertical: space.sm,
                    borderTopWidth: 1,
                    borderTopColor: colors.border,
                  }}
                >
                  <Ionicons name="repeat-outline" size={17} color={colors.textMuted} />
                  <View style={{ flex: 1 }}>
                    <T variant="sm" weight="600">
                      {p.name}
                    </T>
                    <T variant="xs" tone="muted">
                      {(p.blocks || []).length} block{(p.blocks || []).length === 1 ? "" : "s"}
                    </T>
                  </View>
                  <Pressable
                    onPress={() => removePreset(p)}
                    hitSlop={10}
                    accessibilityRole="button"
                    accessibilityLabel={`Delete ${p.name}`}
                  >
                    <Ionicons name="trash-outline" size={17} color={colors.textMuted} />
                  </Pressable>
                </View>
              </Pressable>
            ))}
          </Card>
        ) : null}

        {/* ── The blocks ── */}
        <Card style={{ gap: space.md }}>
          <View>
            <Label>When you're studying</Label>
            <T variant="sm" tone="secondary" style={{ marginTop: 4 }}>
              IntelliPlan fills these with what's due, heaviest first.
            </T>
          </View>

          {blocks.map((b, i) => (
            <View
              key={i}
              style={{
                gap: space.sm,
                paddingTop: i ? space.md : 0,
                borderTopWidth: i ? 1 : 0,
                borderTopColor: colors.border,
              }}
            >
              <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
                <Field
                  placeholder="Study"
                  value={String(b.label ?? "")}
                  onChangeText={(t) => setBlock(i, { label: t })}
                  style={{ flex: 1 }}
                />
                {blocks.length > 1 ? (
                  <Pressable
                    onPress={() => removeBlock(i)}
                    hitSlop={10}
                    accessibilityRole="button"
                    accessibilityLabel={`Remove block ${i + 1}`}
                  >
                    <Ionicons name="close-circle" size={22} color={colors.textMuted} />
                  </Pressable>
                ) : null}
              </View>

              <TimeRow
                label="Starts"
                value={b.start}
                onChange={(v) => setBlock(i, { start: v })}
              />
              <TimeRow label="Ends" value={b.end} onChange={(v) => setBlock(i, { end: v })} />
            </View>
          ))}

          <Button title="Add a block" kind="secondary" icon="add" onPress={addBlock} />
        </Card>

        {/* ── Which days ── */}
        <Card style={{ gap: space.md }}>
          <Label>Which days</Label>
          <View style={{ flexDirection: "row", gap: 6 }}>
            {week.map((d) => {
              const on = days.has(d.iso);
              return (
                <Pressable
                  key={d.iso}
                  onPress={() =>
                    setDays((prev) => {
                      const next = new Set(prev);
                      if (next.has(d.iso)) next.delete(d.iso);
                      else next.add(d.iso);
                      return next;
                    })
                  }
                  accessibilityRole="button"
                  accessibilityState={{ selected: on }}
                  accessibilityLabel={`${d.short} ${d.day}`}
                  style={{
                    flex: 1,
                    alignItems: "center",
                    paddingVertical: space.sm,
                    borderRadius: radius.md,
                    backgroundColor: on ? colors.accent : colors.bgSecondary,
                    borderWidth: 1,
                    borderColor: on ? colors.accent : colors.border,
                  }}
                >
                  <T variant="micro" tone={on ? "onAccent" : "muted"} weight="700">
                    {d.short.toUpperCase()}
                  </T>
                  <T variant="sm" tone={on ? "onAccent" : "primary"} weight="600">
                    {d.day}
                  </T>
                </Pressable>
              );
            })}
          </View>
        </Card>

        {/* ── Save + build ── */}
        <Card style={{ gap: space.md }}>
          <Field
            label="Name this routine"
            placeholder="Weekday evenings"
            value={routineName}
            onChangeText={setRoutineName}
          />
          <View style={{ flexDirection: "row", gap: space.sm }}>
            <Button
              title="Save routine"
              kind="secondary"
              icon="bookmark-outline"
              busy={busy === "save"}
              onPress={saveRoutine}
              style={{ flex: 1 }}
            />
          </View>
          <T variant="xs" tone="muted">
            Saving keeps the blocks for next time. Building applies them to the days
            you picked.
          </T>
        </Card>

        {note ? (
          <Notice
            text={note}
            tone={/Saved|built/i.test(note) ? "accent" : "warn"}
            icon="information-circle-outline"
          />
        ) : null}

        <Button
          title={`Build ${days.size} day${days.size === 1 ? "" : "s"}`}
          icon="calendar"
          busy={busy === "build"}
          onPress={build}
        />
      </ScrollView>
    </Screen>
  );
}

/** A horizontally scrolling half-hour picker. */
function TimeRow({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const { colors } = useTheme();
  const scroller = React.useRef<ScrollView>(null);
  const spans = React.useRef<Record<string, number>>({});
  const revealed = React.useRef(false);

  const reveal = React.useCallback(() => {
    if (revealed.current) return;
    const x = spans.current[value];
    if (x === undefined) return;
    revealed.current = true;
    if (x > 0) scroller.current?.scrollTo({ x: Math.max(0, x - space.lg), animated: false });
  }, [value]);

  return (
    <View style={{ gap: 6 }}>
      <T variant="micro" tone="muted" weight="700" style={{ textTransform: "uppercase" }}>
        {label}
      </T>
      <ScrollView
        ref={scroller}
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ gap: 6, paddingRight: space.lg }}
      >
        {SLOTS.map((slot) => {
          const on = slot === value;
          return (
            <Pressable
              key={slot}
              onPress={() => onChange(slot)}
              onLayout={(e) => {
                spans.current[slot] = e.nativeEvent.layout.x;
                if (on) reveal();
              }}
              accessibilityRole="button"
              accessibilityState={{ selected: on }}
              style={{
                paddingHorizontal: 12,
                paddingVertical: 7,
                borderRadius: radius.pill,
                backgroundColor: on ? colors.accent : colors.bgSecondary,
                borderWidth: 1,
                borderColor: on ? colors.accent : colors.border,
              }}
            >
              <T variant="sm" tone={on ? "onAccent" : "secondary"} weight={on ? "700" : "500"}>
                {label12(slot)}
              </T>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

