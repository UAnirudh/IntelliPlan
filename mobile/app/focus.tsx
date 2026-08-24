import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, AppState, Pressable, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import Svg, { Circle } from "react-native-svg";
import {
  dismissTask,
  finishSession,
  FocusSession,
  getCurrentSession,
  heartbeatSession,
  startSession,
} from "../lib/api";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, Chip, Label, Notice, Screen, SegmentedRow, T } from "../components/ui";
import { useConfirm } from "../components/Confirm";

const DURATIONS = [
  { label: "15 min", value: 15 },
  { label: "25 min", value: 25 },
  { label: "45 min", value: 45 },
  { label: "60 min", value: 60 },
  { label: "90 min", value: 90 },
];

/** Elapsed time is pushed to the server on this cadence while running. */
const HEARTBEAT_MS = 15_000;

function mmss(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

/**
 * A focus session: start the clock on one piece of work, and tell the
 * server what actually happened.
 *
 * The server is the record, not the phone. Elapsed time is sent as a
 * running total rather than a delta, so a heartbeat lost in a dead spot
 * costs nothing — and the server clamps the figure against wall-clock
 * time, so a phone with a wrong clock cannot inflate a session.
 */
export default function FocusScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const confirm = useConfirm();
  const params = useLocalSearchParams<{
    title?: string;
    course?: string;
    taskId?: string;
    dueDate?: string;
    minutes?: string;
  }>();

  const [session, setSession] = useState<FocusSession | null>(null);
  const [booting, setBooting] = useState(true);
  const [planned, setPlanned] = useState(() => {
    const asked = Number(params.minutes) || 25;
    const options = DURATIONS.map((d) => d.value);
    // Snap to the nearest offered length: a task estimated at 38 minutes
    // would otherwise select no chip at all, and the row would look empty
    // until the student happened to tap one.
    return options.reduce((best, v) => (Math.abs(v - asked) < Math.abs(best - asked) ? v : best), options[0]);
  });
  const [starting, setStarting] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  /* ── The clock ───────────────────────────────────────────────────
     Anchored to wall-clock timestamps rather than counted ticks: a
     setInterval in a backgrounded app is throttled or suspended, so a
     tick counter loses minutes every time the student looks at
     something else. `tick` exists only to force a re-render.          */
  const accumulated = useRef(0);      // seconds banked before the current run
  const resumedAt = useRef<number | null>(null); // ms, or null when paused
  const pausedSeconds = useRef(0);
  const pausedAt = useRef<number | null>(null);
  const pauseCount = useRef(0);
  const [, setTick] = useState(0);

  const elapsed = useCallback(() => {
    const live = resumedAt.current ? (Date.now() - resumedAt.current) / 1000 : 0;
    return accumulated.current + live;
  }, []);

  const totalPaused = useCallback(() => {
    const live = pausedAt.current ? (Date.now() - pausedAt.current) / 1000 : 0;
    return pausedSeconds.current + live;
  }, []);

  /* ── Boot: resume, or start a new session ───────────────────────── */
  useEffect(() => {
    (async () => {
      try {
        const existing = await getCurrentSession();
        if (existing) {
          setSession(existing);
          accumulated.current = existing.active_seconds || 0;
          pausedSeconds.current = existing.paused_seconds || 0;
          pauseCount.current = existing.pause_count || 0;
          // Deliberately resumed as paused even if the server says
          // "running": the app was closed, and we have no idea whether the
          // student was working through that gap. Counting it would be
          // inventing study time. They tap resume when they're back.
          resumedAt.current = null;
          pausedAt.current = Date.now();
          setRunning(false);
        }
      } catch (e: any) {
        setError(e?.message || null);
      } finally {
        setBooting(false);
      }
    })();
  }, []);

  /* ── Re-render once a second while the clock is live ─────────────── */
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [running]);

  /* ── Recompute immediately on foreground ─────────────────────────
     Coming back from the background, the numbers on screen are as stale
     as however long the app was away. The anchors already hold the
     truth; this just repaints with it.                                */
  useEffect(() => {
    const sub = AppState.addEventListener("change", (s) => {
      if (s === "active") setTick((n) => n + 1);
    });
    return () => sub.remove();
  }, []);

  /* ── Heartbeat ───────────────────────────────────────────────────── */
  useEffect(() => {
    if (!session || !running) return;
    const id = setInterval(() => {
      heartbeatSession(session.id, {
        activeSeconds: elapsed(),
        pausedSeconds: totalPaused(),
        pauseCount: pauseCount.current,
        state: "running",
      }).catch(() => {
        // A dropped heartbeat is not worth interrupting a study session
        // over. The next one carries the running total, and `finish`
        // sends the authoritative figure regardless.
      });
    }, HEARTBEAT_MS);
    return () => clearInterval(id);
  }, [session, running, elapsed, totalPaused]);

  const begin = useCallback(async () => {
    const title = (params.title || "").trim() || "Study session";
    setStarting(true);
    setError(null);
    try {
      const fresh = await startSession({
        title,
        plannedMinutes: planned,
        course: params.course,
        taskId: params.taskId,
        dueDate: params.dueDate || null,
      });
      setSession(fresh);
      accumulated.current = 0;
      pausedSeconds.current = 0;
      pauseCount.current = 0;
      pausedAt.current = null;
      resumedAt.current = Date.now();
      setRunning(true);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "Couldn't start the session.");
    } finally {
      setStarting(false);
    }
  }, [params.title, params.course, params.taskId, params.dueDate, planned]);

  const pause = useCallback(() => {
    if (!running) return;
    accumulated.current = elapsed();
    resumedAt.current = null;
    pausedAt.current = Date.now();
    pauseCount.current += 1;
    setRunning(false);
    Haptics.selectionAsync().catch(() => {});
    if (session) {
      heartbeatSession(session.id, {
        activeSeconds: accumulated.current,
        pausedSeconds: pausedSeconds.current,
        pauseCount: pauseCount.current,
        state: "paused",
      }).catch(() => {});
    }
  }, [running, elapsed, session]);

  const resume = useCallback(() => {
    if (running) return;
    pausedSeconds.current = totalPaused();
    pausedAt.current = null;
    resumedAt.current = Date.now();
    setRunning(true);
    Haptics.selectionAsync().catch(() => {});
  }, [running, totalPaused]);

  const end = useCallback(
    async (completed: boolean, difficulty: number | null) => {
      if (!session) return;
      setFinishing(true);
      const seconds = elapsed();
      try {
        await finishSession(session.id, {
          completed,
          activeSeconds: seconds,
          progressPercent: completed ? 100 : Math.min(99, Math.round((seconds / (planned * 60)) * 100)),
          perceivedDifficulty: difficulty,
        });
        // Ticking the assignment off is a separate call because a session
        // and an assignment are separate things: you can finish an hour of
        // work without finishing the essay.
        if (completed && params.taskId && params.title) {
          await dismissTask(params.title).catch(() => {});
        }
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
        router.back();
      } catch (e: any) {
        setError(e?.message || "Couldn't save the session.");
        setFinishing(false);
      }
    },
    [session, elapsed, planned, params.taskId, params.title, router],
  );

  const askHowItWent = useCallback(async () => {
    // Paused first, so the clock is not still counting while the student
    // decides — those seconds were not study time.
    pause();
    const choice = await confirm({
      title: "How did that go?",
      message: "Did you finish what you sat down to do?",
      actions: [
        { label: "Finished it" },
        { label: "Made progress" },
        { label: "Keep going", cancel: true },
      ],
    });
    if (choice === 0) end(true, null);
    else if (choice === 1) end(false, null);
    else resume();
  }, [pause, resume, end, confirm]);

  const abandon = useCallback(async () => {
    const choice = await confirm({
      title: "Give up on this session?",
      message: "The time you did put in is still recorded.",
      actions: [
        { label: "Give up", destructive: true },
        { label: "Keep going", cancel: true },
      ],
    });
    if (choice === 0) end(false, null);
  }, [end, confirm]);

  const seconds = elapsed();
  const target = planned * 60;
  const remaining = Math.max(0, target - seconds);
  const overrun = seconds > target;

  return (
    <Screen>
      <View
        style={{
          flexDirection: "row",
          alignItems: "center",
          paddingTop: insets.top + space.sm,
          paddingHorizontal: space.lg,
          paddingBottom: space.md,
        }}
      >
        <T variant="lg" weight="700" style={{ flex: 1 }}>
          Focus
        </T>
        <Pressable
          onPress={() => (session ? askHowItWent() : router.back())}
          hitSlop={10}
          accessibilityRole="button"
          accessibilityLabel={session ? "End session" : "Close"}
        >
          <Ionicons name="close" size={26} color={colors.textSecondary} />
        </Pressable>
      </View>

      <ScrollView
        contentContainerStyle={{
          padding: space.lg,
          paddingBottom: insets.bottom + 40,
          gap: space.lg,
          flexGrow: 1,
        }}
      >
        {booting ? (
          <View style={{ flex: 1, justifyContent: "center" }}>
            <ActivityIndicator color={colors.accent} />
          </View>
        ) : (
          <>
            <Card style={{ gap: 6 }}>
              <Label>{session ? "Working on" : "About to start"}</Label>
              <T variant="md" weight="700">
                {session?.title || params.title || "Study session"}
              </T>
              <View style={{ flexDirection: "row", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                {(session?.course || params.course) ? (
                  <Chip label={String(session?.course || params.course)} />
                ) : null}
                <Chip label={`${planned} min planned`} icon="hourglass-outline" />
                {pauseCount.current ? (
                  <Chip label={`${pauseCount.current} pause${pauseCount.current === 1 ? "" : "s"}`} icon="pause" />
                ) : null}
              </View>
            </Card>

            {error ? <Notice text={error} icon="alert-circle-outline" /> : null}

            {!session ? (
              <>
                <View style={{ gap: space.sm }}>
                  <Label>How long</Label>
                  <SegmentedRow options={DURATIONS} value={planned} onChange={setPlanned} />
                </View>
                <Button title="Start focusing" icon="play" busy={starting} onPress={begin} />
                <T variant="xs" tone="muted" style={{ textAlign: "center" }}>
                  The clock keeps running if you switch apps — it's anchored to the
                  time of day, not to this screen being open.
                </T>
              </>
            ) : (
              <>
                <View style={{ alignItems: "center", gap: space.md, paddingVertical: space.lg }}>
                  <Dial
                    progress={Math.min(1, seconds / target)}
                    colour={overrun ? colors.ok : running ? colors.accent : colors.textMuted}
                    track={colors.bgElevated}
                  >
                    <T variant="xxl" weight="700" style={{ fontVariant: ["tabular-nums"] }}>
                      {overrun ? mmss(seconds) : mmss(remaining)}
                    </T>
                    <T variant="xs" tone="muted">
                      {overrun ? "past your target" : running ? "remaining" : "paused"}
                    </T>
                  </Dial>

                  <T variant="sm" tone="secondary">
                    {mmss(seconds)} focused
                    {totalPaused() > 30 ? ` · ${mmss(totalPaused())} paused` : ""}
                  </T>
                </View>

                <View style={{ flexDirection: "row", gap: space.md }}>
                  <Button
                    title={running ? "Pause" : "Resume"}
                    icon={running ? "pause" : "play"}
                    kind="secondary"
                    onPress={running ? pause : resume}
                    style={{ flex: 1 }}
                  />
                  <Button
                    title="Done"
                    icon="checkmark"
                    busy={finishing}
                    onPress={askHowItWent}
                    style={{ flex: 1 }}
                  />
                </View>

                <Pressable onPress={abandon} hitSlop={8} accessibilityRole="button" style={{ alignSelf: "center", padding: space.sm }}>
                  <T variant="sm" tone="muted">
                    Give up on this one
                  </T>
                </Pressable>
              </>
            )}
          </>
        )}
      </ScrollView>
    </Screen>
  );
}

function Dial({
  progress,
  colour,
  track,
  children,
}: {
  progress: number;
  colour: string;
  track: string;
  children: React.ReactNode;
}) {
  const size = 232;
  const stroke = 14;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <Svg width={size} height={size} style={{ position: "absolute" }}>
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={track} strokeWidth={stroke} fill="none" />
        <Circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={colour}
          strokeWidth={stroke}
          strokeLinecap="round"
          fill="none"
          strokeDasharray={`${c} ${c}`}
          strokeDashoffset={c * (1 - Math.max(0, Math.min(1, progress)))}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </Svg>
      <View style={{ alignItems: "center", gap: 2 }}>{children}</View>
    </View>
  );
}

