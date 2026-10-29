import React, { useCallback, useMemo, useState } from "react";
import { Pressable, RefreshControl, ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import {
  addFlashcards,
  createFlashcardDeck,
  Flashcard,
  FlashcardDeck,
  FlashcardDecks,
  getFlashcardDecks,
  getFlashcardQueue,
  gradeFlashcard,
  importFlashcardText,
} from "../lib/api";
import { useQuery } from "../lib/useQuery";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import { Button, Card, EmptyState, ErrorState, Field, Label, Loading, Notice, Screen, SegmentedRow, T } from "../components/ui";

type Mode = "library" | "study" | "create" | "import" | "add";
type Rating = 1 | 2 | 3 | 4;

const RATINGS: { label: string; value: Rating }[] = [
  { label: "Again", value: 1 },
  { label: "Hard", value: 2 },
  { label: "Good", value: 3 },
  { label: "Easy", value: 4 },
];

/** Saved, portable decks and a complete native spaced-repetition review loop. */
export default function FlashcardsScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const params = useLocalSearchParams<{ deckId?: string }>();
  const decksQuery = useQuery<FlashcardDecks>("flashcard-decks", getFlashcardDecks);
  const requestedDeckId = Number(params.deckId);
  const initialDeckId = Number.isSafeInteger(requestedDeckId) && requestedDeckId > 0 ? requestedDeckId : undefined;
  const [mode, setMode] = useState<Mode>(initialDeckId ? "study" : "library");
  const [deckId, setDeckId] = useState<number | undefined>(initialDeckId);
  const [cardIndex, setCardIndex] = useState(0);
  const [showAnswer, setShowAnswer] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deckName, setDeckName] = useState("");
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [importName, setImportName] = useState("");
  const [importText, setImportText] = useState("");
  const [importFormat, setImportFormat] = useState<"quizlet" | "csv">("quizlet");

  const queueKey = `flashcard-queue-${deckId || "all"}`;
  const queue = useQuery<{ cards: Flashcard[]; count: number; deckId: number | null }>(
    queueKey,
    async () => ({ ...(await getFlashcardQueue(deckId)), deckId: deckId ?? null }),
    { enabled: mode === "study" },
  );
  // useQuery keeps the previous response while a new key is loading. Include
  // the selected deck in the payload so a fast deck switch cannot show or
  // grade another deck's first card under the new deck title.
  const queueMatchesDeck = queue.data?.deckId === (deckId ?? null);
  const cards = queueMatchesDeck ? queue.data?.cards || [] : [];
  const current = cards[cardIndex];
  const selectedDeck = useMemo(
    () => decksQuery.data?.decks.find((deck) => deck.id === deckId),
    [deckId, decksQuery.data?.decks],
  );

  const enterStudy = useCallback((selected?: FlashcardDeck) => {
    setDeckId(selected?.id);
    setCardIndex(0);
    setShowAnswer(false);
    setError(null);
    setMode("study");
  }, []);

  const close = useCallback(() => {
    if (mode !== "library") {
      setMode("library");
      setError(null);
      setNotice(null);
      return;
    }
    if (router.canGoBack()) router.back();
    else router.replace("/(tabs)/more");
  }, [mode, router]);

  const createDeck = useCallback(async () => {
    if (!deckName.trim()) {
      setError("Add a name for this deck.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await createFlashcardDeck(deckName.trim());
      await decksQuery.reload();
      setDeckId(result.id);
      setDeckName("");
      setMode("add");
      setNotice("Deck created. Add your first card.");
    } catch (e: any) {
      setError(e?.message || "Couldn't create this deck.");
    } finally {
      setBusy(false);
    }
  }, [deckName, decksQuery]);

  const addCard = useCallback(async () => {
    if (!deckId || !front.trim() || !back.trim()) {
      setError("Add both a question and an answer.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await addFlashcards(deckId, [{ front: front.trim(), back: back.trim() }]);
      await decksQuery.reload();
      setFront("");
      setBack("");
      setNotice("Card added.");
    } catch (e: any) {
      setError(e?.message || "Couldn't add this card.");
    } finally {
      setBusy(false);
    }
  }, [back, deckId, decksQuery, front]);

  const importDeck = useCallback(async () => {
    if (!importText.trim()) {
      setError("Paste your cards first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await importFlashcardText({
        text: importText,
        name: importName.trim() || undefined,
        format: importFormat,
      });
      await decksQuery.reload();
      setDeckId(result.deck_id);
      setImportName("");
      setImportText("");
      setNotice(`${result.added} cards imported into ${result.name}.`);
      setMode("library");
    } catch (e: any) {
      setError(e?.message || "Couldn't import those cards.");
    } finally {
      setBusy(false);
    }
  }, [decksQuery, importFormat, importName, importText]);

  const grade = useCallback(async (rating: Rating) => {
    if (!current || busy) return;
    setBusy(true);
    setError(null);
    try {
      await gradeFlashcard(current.id, rating);
      Haptics.selectionAsync().catch(() => {});
      // Keep each card to one turn in this session, even when Again schedules
      // it immediately. It can return in the next review session.
      queue.setData((previous) => previous
        ? { ...previous, cards: previous.cards.filter((card) => card.id !== current.id), count: Math.max(0, previous.count - 1) }
        : previous);
      setCardIndex(0);
      setShowAnswer(false);
      decksQuery.reload().catch(() => {});
    } catch (e: any) {
      setError(e?.message || "Couldn't save your review. Try again.");
    } finally {
      setBusy(false);
    }
  }, [busy, current, decksQuery, queue]);

  const header = (
    <View style={{ flexDirection: "row", alignItems: "center", paddingTop: insets.top + space.sm, paddingHorizontal: space.lg, paddingBottom: space.md, borderBottomWidth: 1, borderBottomColor: colors.border }}>
      <Pressable onPress={close} accessibilityRole="button" accessibilityLabel={mode === "library" ? "Close flashcards" : "Back to saved decks"} style={{ width: 44, height: 44, marginLeft: -8, alignItems: "center", justifyContent: "center" }}>
        <Ionicons name={mode === "library" ? "close" : "arrow-back"} size={24} color={colors.textSecondary} />
      </Pressable>
      <View style={{ flex: 1, marginLeft: space.xs }}>
        <T variant="lg" weight="700">{mode === "study" ? selectedDeck?.name || "Review cards" : mode === "library" ? "Flashcards" : mode === "create" ? "New deck" : mode === "import" ? "Import cards" : "Add a card"}</T>
        <T variant="xs" tone="muted">{mode === "study" ? "Remember more, a little at a time" : "Your study sets, ready whenever you are"}</T>
      </View>
    </View>
  );

  return (
    <Screen>
      {header}
      {mode === "library" ? (
        <ScrollView contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: insets.bottom + 100 }} refreshControl={<RefreshControl refreshing={decksQuery.refreshing} onRefresh={decksQuery.refresh} tintColor={colors.accent} />}>
          {decksQuery.stale ? <Notice text="Offline — showing the last decks that loaded." icon="cloud-offline-outline" /> : null}
          {notice ? <Notice tone="accent" text={notice} icon="checkmark-circle-outline" /> : null}
          {error ? <Notice text={error} icon="alert-circle-outline" /> : null}
          <Card style={{ gap: space.md }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
              <View style={{ width: 52, height: 52, borderRadius: radius.lg, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" }}>
                <Ionicons name="copy-outline" size={24} color={colors.accent} />
              </View>
              <View style={{ flex: 1 }}>
                <T variant="lg" weight="700">{decksQuery.data?.stats.due || 0} to review</T>
                <T variant="sm" tone="muted">{decksQuery.data?.stats.new || 0} new · {decksQuery.data?.stats.cards || 0} cards total</T>
              </View>
            </View>
            <Button title="Review all decks" icon="play" onPress={() => enterStudy()} disabled={!decksQuery.data?.stats.cards} />
          </Card>

          <View style={{ flexDirection: "row", alignItems: "center", marginTop: space.xs }}>
            <T variant="lg" weight="700" style={{ flex: 1 }}>Your decks</T>
            <Pressable onPress={() => { setError(null); setMode("import"); }} accessibilityRole="button" style={{ minHeight: 44, justifyContent: "center", paddingHorizontal: space.sm }}>
              <T tone="accent" weight="600">Import</T>
            </Pressable>
            <Pressable onPress={() => { setError(null); setMode("create"); }} accessibilityRole="button" style={{ minHeight: 44, justifyContent: "center", paddingLeft: space.sm }}>
              <T tone="accent" weight="600">New</T>
            </Pressable>
          </View>

          {decksQuery.loading ? <Loading label="Loading your decks…" /> : decksQuery.error && !decksQuery.data ? <ErrorState message={decksQuery.error} onRetry={decksQuery.reload} /> : null}
          {!decksQuery.loading && !decksQuery.data?.decks.length ? (
            <EmptyState icon="copy-outline" title="Start your first deck" body="Make cards from your notes or paste in a set you already use." action={<View style={{ gap: space.sm, width: "100%" }}><Button title="Create a deck" onPress={() => setMode("create")} /><Button title="Import cards" kind="secondary" onPress={() => setMode("import")} /></View>} />
          ) : null}
          {(decksQuery.data?.decks || []).map((deck) => (
            <DeckRow key={deck.id} deck={deck} onStudy={() => enterStudy(deck)} onAdd={() => { setDeckId(deck.id); setMode("add"); setError(null); }} />
          ))}
        </ScrollView>
      ) : null}

      {mode === "study" ? (
        <ScrollView contentContainerStyle={{ flexGrow: 1, padding: space.lg, paddingBottom: insets.bottom + 100, gap: space.md, justifyContent: "center" }} refreshControl={<RefreshControl refreshing={queue.refreshing} onRefresh={queue.refresh} tintColor={colors.accent} />}>
          {queue.stale && queueMatchesDeck ? <Notice text="Offline — these cards are from your last review load. Ratings need a connection." icon="cloud-offline-outline" /> : null}
          {error ? <Notice text={error} icon="alert-circle-outline" /> : null}
          {!queueMatchesDeck && !queue.error ? <Loading label="Getting your cards…" /> : null}
          {queue.error && !queueMatchesDeck ? <ErrorState message={queue.error} onRetry={queue.reload} /> : null}
          {current ? <>
            <View style={{ alignItems: "center", gap: 4 }}>
              <Label>{selectedDeck?.name || "All decks"}</Label>
              <T variant="xs" tone="muted">{cards.length} card{cards.length === 1 ? "" : "s"} in this session</T>
            </View>
            <Pressable onPress={() => setShowAnswer((value) => !value)} accessibilityRole="button" accessibilityLabel={showAnswer ? "Hide answer" : "Show answer"} accessibilityHint="Tap the card to reveal or hide the answer" style={{ minHeight: 300 }}>
              <Card style={{ minHeight: 300, justifyContent: "center", alignItems: "center", gap: space.lg, padding: space.xl, borderColor: colors.borderStrong }}>
                <T variant="xs" tone="muted">{showAnswer ? "ANSWER" : "QUESTION"}</T>
                <T variant="xl" weight="700" style={{ textAlign: "center" }}>{showAnswer ? current.back : current.front}</T>
                {showAnswer && current.extra ? <T variant="sm" tone="muted" style={{ textAlign: "center" }}>{current.extra}</T> : null}
                <T variant="xs" tone="accent">Tap to {showAnswer ? "see question" : "show answer"}</T>
              </Card>
            </Pressable>
            {showAnswer ? <>
              <T variant="sm" tone="muted" style={{ textAlign: "center" }}>How well did you remember?</T>
              <View style={{ flexDirection: "row", gap: space.xs }}>
                {RATINGS.map((rating) => <View key={rating.value} style={{ flex: 1 }}><Button title={rating.label} kind={rating.value === 1 ? "secondary" : "primary"} busy={busy && rating.value === 3} disabled={busy || queue.stale} onPress={() => grade(rating.value)} style={{ paddingHorizontal: 4 }} /></View>)}
              </View>
            </> : <Button title="Show answer" icon="eye-outline" kind="secondary" onPress={() => setShowAnswer(true)} />}
          </> : queueMatchesDeck && queue.data ? (
            <EmptyState icon="checkmark-circle-outline" title="All caught up" body={deckId ? "There are no cards due in this deck right now." : "You're done for now. Come back when more cards are due."} action={<Button title="Back to decks" onPress={() => setMode("library")} />} />
          ) : null}
        </ScrollView>
      ) : null}

      {mode === "create" ? (
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: space.lg, gap: space.md }}>
          {error ? <Notice text={error} icon="alert-circle-outline" /> : null}
          <Card style={{ gap: space.md }}>
            <T variant="base">Give your study set a clear name so you can find it again.</T>
            <Field label="Deck name" placeholder="e.g. Biology — Cell structure" value={deckName} onChangeText={setDeckName} maxLength={200} returnKeyType="done" onSubmitEditing={createDeck} />
            <Button title="Create deck" busy={busy} onPress={createDeck} />
          </Card>
        </ScrollView>
      ) : null}

      {mode === "add" ? (
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: space.lg, gap: space.md }}>
          {notice ? <Notice tone="accent" text={notice} icon="checkmark-circle-outline" /> : null}
          {error ? <Notice text={error} icon="alert-circle-outline" /> : null}
          <Card style={{ gap: space.md }}>
            <T variant="base">{decksQuery.data?.decks.find((deck) => deck.id === deckId)?.name || "Your deck"}</T>
            <Field label="Front" placeholder="Question or term" value={front} onChangeText={setFront} multiline textAlignVertical="top" style={{ minHeight: 88 }} />
            <Field label="Back" placeholder="Answer or definition" value={back} onChangeText={setBack} multiline textAlignVertical="top" style={{ minHeight: 120 }} />
            <Button title="Add card" icon="add" busy={busy} onPress={addCard} />
            <Button title="Done" kind="secondary" onPress={() => { setMode("library"); setNotice(null); }} />
          </Card>
        </ScrollView>
      ) : null}

      {mode === "import" ? (
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: insets.bottom + 100 }}>
          {error ? <Notice text={error} icon="alert-circle-outline" /> : null}
          <Card style={{ gap: space.md }}>
            <T variant="base" weight="600">Bring your cards with you</T>
            <T variant="sm" tone="muted">Paste tab-separated cards from Quizlet, or CSV rows with a question and answer in each.</T>
            <SegmentedRow options={[{ label: "Quizlet", value: "quizlet" }, { label: "CSV", value: "csv" }]} value={importFormat} onChange={setImportFormat} />
            <Field label="Deck name (optional)" placeholder="Imported study set" value={importName} onChangeText={setImportName} maxLength={200} />
            <Field label="Cards" placeholder={importFormat === "csv" ? "Question,Answer\nWhat is a cell?,The basic unit of life" : "Term 1<TAB>Definition 1\nTerm 2<TAB>Definition 2"} value={importText} onChangeText={setImportText} multiline textAlignVertical="top" style={{ minHeight: 220 }} />
            <Button title="Import cards" icon="download-outline" busy={busy} onPress={importDeck} />
          </Card>
        </ScrollView>
      ) : null}
    </Screen>
  );
}

function DeckRow({ deck, onStudy, onAdd }: { deck: FlashcardDeck; onStudy: () => void; onAdd: () => void }) {
  const { colors } = useTheme();
  return (
    <Card style={{ gap: space.md }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
        <View style={{ width: 42, height: 42, borderRadius: radius.md, backgroundColor: colors.accentSoft, alignItems: "center", justifyContent: "center" }}>
          <Ionicons name="book-outline" size={21} color={colors.accent} />
        </View>
        <View style={{ flex: 1 }}>
          <T variant="base" weight="700" numberOfLines={2}>{deck.name}</T>
          <T variant="xs" tone="muted">{deck.total} card{deck.total === 1 ? "" : "s"} · {deck.due} due · {deck.new} new</T>
        </View>
      </View>
      <View style={{ flexDirection: "row", gap: space.sm }}>
        <View style={{ flex: 1 }}><Button title={deck.due || deck.new ? "Study" : "Review"} icon="play" onPress={onStudy} disabled={!deck.total} /></View>
        <Button title="Add" kind="secondary" icon="add" onPress={onAdd} accessibilityLabel={`Add a card to ${deck.name}`} />
      </View>
    </Card>
  );
}
