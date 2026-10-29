import React, { useMemo, useState } from "react";
import { Pressable, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { T } from "./ui";

type StudyPrompt = { question: string; answer: string };
type StudyMaterial = { title?: string; summary?: string; cards: StudyPrompt[]; quiz: StudyPrompt[] };

/** Render the tutor's note-card JSON as study material inside the conversation. */
export function TutorResponse({ text, tone = "primary" }: { text: string; tone?: "primary" | "onAccent" | "danger" }) {
  const { colors } = useTheme();
  const material = useMemo(() => parseStudyMaterial(text), [text]);
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});

  if (!material) {
    return <T variant="base" tone={tone} style={{ lineHeight: 22 }} selectable>{text}</T>;
  }

  return (
    <View style={{ gap: space.sm }}>
      {material.title ? <T variant="base" weight="700" tone={tone}>{material.title}</T> : null}
      {material.summary ? <T variant="sm" tone={tone}>{material.summary}</T> : null}
      {material.cards.map((card, index) => {
        const key = `card-${index}`;
        const open = !!revealed[key];
        return (
          <Pressable
            key={key}
            onPress={() => setRevealed((current) => ({ ...current, [key]: !current[key] }))}
            accessibilityRole="button"
            accessibilityState={{ expanded: open }}
            accessibilityLabel={`${open ? "Hide" : "Reveal"} answer for flashcard ${index + 1}: ${card.question}`}
            style={{ minHeight: 56, borderWidth: 1, borderColor: tone === "onAccent" ? "rgba(0,0,0,0.2)" : colors.border, backgroundColor: tone === "onAccent" ? "rgba(0,0,0,0.08)" : colors.bgElevated, borderRadius: radius.md, padding: space.sm, gap: space.xs }}
          >
            <View style={{ flexDirection: "row", alignItems: "flex-start", gap: space.xs }}>
              <T variant="xs" weight="700" tone={tone === "onAccent" ? "onAccent" : "accent"}>Q{index + 1}</T>
              <T variant="sm" weight="600" tone={tone} style={{ flex: 1 }}>{card.question}</T>
              <Ionicons name={open ? "chevron-up" : "chevron-down"} size={16} color={tone === "onAccent" ? colors.onAccent : colors.textMuted} />
            </View>
            {open ? <T variant="sm" tone={tone} style={{ paddingLeft: 24, lineHeight: 21 }}>{card.answer}</T> : <T variant="xs" tone={tone === "onAccent" ? "onAccent" : "muted"} style={{ paddingLeft: 24 }}>Tap to reveal</T>}
          </Pressable>
        );
      })}
      {material.quiz.map((item, index) => {
        const key = `quiz-${index}`;
        const open = !!revealed[key];
        return (
          <Pressable
            key={key}
            onPress={() => setRevealed((current) => ({ ...current, [key]: !current[key] }))}
            accessibilityRole="button"
            accessibilityState={{ expanded: open }}
            accessibilityLabel={`${open ? "Hide" : "Reveal"} answer for quiz question ${index + 1}: ${item.question}`}
            style={{ minHeight: 56, borderWidth: 1, borderColor: tone === "onAccent" ? "rgba(0,0,0,0.2)" : colors.border, backgroundColor: tone === "onAccent" ? "rgba(0,0,0,0.08)" : colors.bgElevated, borderRadius: radius.md, padding: space.sm, gap: space.xs }}
          >
            <View style={{ flexDirection: "row", alignItems: "flex-start", gap: space.xs }}>
              <T variant="xs" weight="700" tone={tone === "onAccent" ? "onAccent" : "accent"}>Quiz {index + 1}</T>
              <T variant="sm" weight="600" tone={tone} style={{ flex: 1 }}>{item.question}</T>
              <Ionicons name={open ? "chevron-up" : "chevron-down"} size={16} color={tone === "onAccent" ? colors.onAccent : colors.textMuted} />
            </View>
            {open ? <T variant="sm" tone={tone} style={{ paddingLeft: 24, lineHeight: 21 }}>{item.answer}</T> : <T variant="xs" tone={tone === "onAccent" ? "onAccent" : "muted"} style={{ paddingLeft: 24 }}>Show answer</T>}
          </Pressable>
        );
      })}
    </View>
  );
}

function parseStudyMaterial(raw: string): StudyMaterial | null {
  const candidate = raw.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  if (!candidate.startsWith("{") || !candidate.endsWith("}")) return null;
  try {
    const data = JSON.parse(candidate);
    if (!data || Array.isArray(data) || typeof data !== "object") return null;
    const cards = readPrompts(data.flashcards ?? data.note_cards ?? data.cards);
    const quiz = readPrompts(data.quiz ?? data.questions);
    if (!cards.length && !quiz.length) return null;
    return {
      title: safeText(data.title, 120),
      summary: safeText(data.summary, 1200),
      cards: cards.slice(0, 12),
      quiz: quiz.slice(0, 12),
    };
  } catch {
    return null;
  }
}

function readPrompts(value: unknown): StudyPrompt[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 12).flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    const question = safeText(row.question ?? row.front ?? row.prompt ?? row.term, 1000);
    const answer = safeText(row.answer ?? row.back ?? row.definition ?? row.explanation, 2000);
    return question && answer ? [{ question, answer }] : [];
  });
}

function safeText(value: unknown, maxLength: number): string {
  return typeof value === "string" ? value.trim().slice(0, maxLength) : "";
}
