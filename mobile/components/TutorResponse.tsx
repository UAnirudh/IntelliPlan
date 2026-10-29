import React, { useMemo, useRef, useState } from "react";
import { Pressable, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { addFlashcards, createFlashcardDeck, getFlashcardDecks } from "../lib/api";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, T } from "./ui";

type StudyPrompt = { question: string; answer: string };
type StudyMaterial = { title?: string; summary?: string; cards: StudyPrompt[]; quiz: StudyPrompt[] };

/** Render the tutor's note-card JSON as study material inside the conversation. */
export function TutorResponse({ text, tone = "primary" }: { text: string; tone?: "primary" | "onAccent" | "danger" }) {
  const { colors } = useTheme();
  const router = useRouter();
  const material = useMemo(() => parseStudyMaterial(text), [text]);
  const saveReference = useRef(`tutor-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`);
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [deckId, setDeckId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [retrySafe, setRetrySafe] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  if (!material) {
    return <T variant="base" tone={tone} style={{ lineHeight: 22 }} selectable>{text}</T>;
  }

  const openDeck = (id?: number | null) => router.push(id
    ? { pathname: "/flashcards", params: { deckId: String(id) } }
    : "/flashcards");

  const saveCards = async () => {
    if (saving || saved || !material.cards.length) return;
    setSaving(true);
    setSaveError(null);
    setRetrySafe(false);
    let targetDeckId = deckId;
    try {
      let library = await getFlashcardDecks();
      if (!targetDeckId) {
        // If deck creation succeeded but its response was lost, locate the
        // deck through its private reference instead of creating an orphan.
        const existing = library.decks.find((deck) => deck.description === saveReference.current);
        if (existing) targetDeckId = existing.id;
      }
      if (!targetDeckId) {
        const name = (material.title ? `Plani: ${material.title}` : "Plani study cards").slice(0, 200);
        const deck = await createFlashcardDeck(name, saveReference.current);
        targetDeckId = deck.id;
        setDeckId(deck.id);
        library = await getFlashcardDecks();
      }
      if (targetDeckId) setDeckId(targetDeckId);

      // A previous response may have been lost after the server committed.
      // Check the newly created deck before retrying so cards can't be doubled.
      const currentCount = library.decks.find((deck) => deck.id === targetDeckId)?.total;
      if (currentCount === material.cards.length) {
        setSaved(true);
        return;
      }
      if (currentCount !== 0) {
        setSaveError("I couldn't confirm this set safely. Open the deck and check its cards before trying again.");
        return;
      }

      const result = await addFlashcards(targetDeckId, material.cards.map((card) => ({
        front: card.question,
        back: card.answer,
      })));
      if (result.added !== material.cards.length) {
        setSaveError("Some cards could not be saved. Open the deck to review it.");
        return;
      }
      setSaved(true);
    } catch (error: any) {
      setSaveError(error?.message || "Couldn't save these flashcards right now.");
      try {
        const library = await getFlashcardDecks();
        const recoveredDeck = targetDeckId
          ? library.decks.find((deck) => deck.id === targetDeckId)
          : library.decks.find((deck) => deck.description === saveReference.current);
        if (recoveredDeck) {
          setDeckId(recoveredDeck.id);
          if (recoveredDeck.total === material.cards.length) setSaved(true);
          else if (recoveredDeck.total === 0) setRetrySafe(true);
        }
      } catch {
        // Leave retries disabled until the saved-card state can be checked.
      }
    } finally {
      setSaving(false);
    }
  };

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
      {material.cards.length ? (
        <View style={{ gap: space.xs, paddingTop: space.xs }}>
          {saveError ? (
            <T variant="xs" tone="danger" accessibilityLiveRegion="polite">{saveError}</T>
          ) : null}
          {saved ? (
            <Button
              title="Open saved deck"
              icon="checkmark-done-outline"
              kind="secondary"
              onPress={() => openDeck(deckId)}
            />
          ) : deckId && !retrySafe && saveError ? (
            <Button title="Check flashcard decks" icon="library-outline" kind="secondary" onPress={() => openDeck(deckId)} />
          ) : (
            <Button
              title={retrySafe ? "Try saving again" : `Save ${material.cards.length} flashcard${material.cards.length === 1 ? "" : "s"}`}
              icon={retrySafe ? "refresh-outline" : "bookmark-outline"}
              kind="secondary"
              busy={saving}
              disabled={saving}
              accessibilityHint="Saves these cards to your review decks"
              onPress={saveCards}
            />
          )}
        </View>
      ) : null}
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
