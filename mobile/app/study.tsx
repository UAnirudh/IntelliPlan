import React, { useCallback, useMemo, useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import * as DocumentPicker from "expo-document-picker";
import {
  Evaluation,
  evaluateAnswer,
  extractPdfText,
  extractYoutubeText,
  generateStudySet,
  StudyQuestion,
  StudySet,
} from "../lib/api";
import { useTheme } from "../theme/ThemeProvider";
import { radius, space } from "../theme/tokens";
import {
  Button,
  Card,
  Chip,
  Field,
  Label,
  Loading,
  Notice,
  Screen,
  SegmentedRow,
  T,
} from "../components/ui";

/**
 * Study tools.
 *
 * Three ways in and one pipeline out: pasting notes, picking a PDF, and
 * dropping a YouTube link all produce a string of study material, which
 * `/study/generate` turns into key concepts and questions. Flashcards and
 * the quiz are two views of that one result, not two generations — asking
 * the model twice would cost twice and could disagree with itself.
 */

type Source = "paste" | "pdf" | "youtube";

const SOURCES = [
  { label: "Paste", value: "paste" as Source },
  { label: "PDF", value: "pdf" as Source },
  { label: "YouTube", value: "youtube" as Source },
];

const COUNTS = [
  { label: "5", value: 5 },
  { label: "8", value: 8 },
  { label: "12", value: 12 },
];

const VERDICT_TONE: Record<string, "ok" | "warn" | "danger"> = {
  correct: "ok",
  partial: "warn",
  incorrect: "danger",
};

export default function StudyScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const [source, setSource] = useState<Source>("paste");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [pdfName, setPdfName] = useState<string | null>(null);
  const [count, setCount] = useState(8);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [set, setSet] = useState<StudySet | null>(null);
  const [tab, setTab] = useState<"cards" | "quiz">("cards");

  const cards = set?.key_concepts || [];
  const questions = useMemo(() => set?.questions || [], [set]);

  /* ── Getting the material in ───────────────────────────────────── */

  const pickPdf = useCallback(async () => {
    setError(null);
    try {
      const res = await DocumentPicker.getDocumentAsync({
        type: "application/pdf",
        copyToCacheDirectory: true,
      });
      if (res.canceled) return;
      const file = res.assets?.[0];
      if (!file) return;
      setBusy("pdf");
      setPdfName(file.name || "notes.pdf");
      const extracted = await extractPdfText({
        uri: file.uri,
        name: file.name,
        mimeType: file.mimeType,
      });
      if (!extracted.trim()) {
        // A scanned PDF is images, and PyPDF2 pulls nothing out of it.
        // Saying "no text" is more useful than an empty box.
        setError("That PDF has no selectable text — it may be a scan. Paste the text instead.");
        return;
      }
      setText(extracted);
      setSource("paste");
    } catch (e: any) {
      setError(e?.message || "Couldn't read that PDF.");
    } finally {
      setBusy(null);
    }
  }, []);

  const fetchTranscript = useCallback(async () => {
    if (!url.trim()) {
      setError("Paste a YouTube link first.");
      return;
    }
    setBusy("youtube");
    setError(null);
    try {
      const extracted = await extractYoutubeText(url.trim());
      setText(extracted);
      setSource("paste");
    } catch (e: any) {
      setError(e?.message || "Couldn't fetch that video's transcript.");
    } finally {
      setBusy(null);
    }
  }, [url]);

  const generate = useCallback(async () => {
    const content = text.trim();
    if (content.length < 40) {
      setError("Add a bit more material — a paragraph or two is enough to work with.");
      return;
    }
    setBusy("generate");
    setError(null);
    try {
      const result = await generateStudySet(content, count);
      setSet(result);
      setTab((result.key_concepts || []).length ? "cards" : "quiz");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setError(e?.message || "Couldn't build a study set from that.");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
    } finally {
      setBusy(null);
    }
  }, [text, count]);

  const startOver = useCallback(() => {
    setSet(null);
    setText("");
    setUrl("");
    setPdfName(null);
    setError(null);
  }, []);

  /* ── Rendering ─────────────────────────────────────────────────── */

  const header = (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: space.md,
        paddingTop: insets.top + space.sm,
        paddingHorizontal: space.lg,
        paddingBottom: space.md,
        borderBottomWidth: 1,
        borderBottomColor: colors.border,
      }}
    >
      <View style={{ flex: 1 }}>
        <T variant="lg" weight="700">
          {set?.title || "Study tools"}
        </T>
        <T variant="sm" tone="muted">
          {set ? `${cards.length} cards · ${questions.length} questions` : "Turn anything into flashcards and a quiz."}
        </T>
      </View>
      {set ? (
        <Pressable onPress={startOver} hitSlop={10} accessibilityRole="button" accessibilityLabel="Start over">
          <Ionicons name="refresh" size={22} color={colors.textSecondary} />
        </Pressable>
      ) : null}
      <Pressable onPress={() => router.back()} hitSlop={10} accessibilityRole="button" accessibilityLabel="Close">
        <Ionicons name="close" size={26} color={colors.textSecondary} />
      </Pressable>
    </View>
  );

  if (busy === "generate") {
    return (
      <Screen>
        {header}
        <Loading label="Reading your material…" />
      </Screen>
    );
  }

  return (
    <Screen>
      {header}
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView
          contentContainerStyle={{ padding: space.lg, gap: space.md, paddingBottom: insets.bottom + 60 }}
          keyboardShouldPersistTaps="handled"
        >
          {error ? <Notice text={error} icon="alert-circle-outline" /> : null}

          {!set ? (
            <>
              <Card style={{ gap: space.md }}>
                <Label>Where it's coming from</Label>
                <SegmentedRow options={SOURCES} value={source} onChange={setSource} />

                {source === "paste" ? (
                  <View style={{ gap: space.sm }}>
                    <Label>Your notes</Label>
                    <TextInput
                      value={text}
                      onChangeText={setText}
                      multiline
                      placeholder="Paste a chapter, your lecture notes, a study guide…"
                      placeholderTextColor={colors.textMuted}
                      accessibilityLabel="Study material"
                      style={{
                        minHeight: 180,
                        borderWidth: 1,
                        borderColor: colors.border,
                        borderRadius: radius.md,
                        padding: space.md,
                        color: colors.textPrimary,
                        backgroundColor: colors.bgCard,
                        textAlignVertical: "top",
                      }}
                    />
                    <T variant="xs" tone="muted">
                      {text.trim().length
                        ? `${text.trim().length.toLocaleString()} characters`
                        : "A paragraph or two is enough."}
                      {pdfName ? ` · from ${pdfName}` : ""}
                    </T>
                  </View>
                ) : null}

                {source === "pdf" ? (
                  <View style={{ gap: space.sm }}>
                    <T variant="sm" tone="secondary">
                      Pick a PDF and IntelliPlan pulls the text out of it. Scanned pages
                      won't work — those are pictures, not text.
                    </T>
                    <Button
                      title={pdfName ? `Pick a different PDF` : "Choose a PDF"}
                      icon="document-outline"
                      kind="secondary"
                      busy={busy === "pdf"}
                      onPress={pickPdf}
                    />
                    {pdfName ? <Chip label={pdfName} icon="document-text-outline" /> : null}
                  </View>
                ) : null}

                {source === "youtube" ? (
                  <View style={{ gap: space.sm }}>
                    <Field
                      label="YouTube link"
                      placeholder="https://youtube.com/watch?v=…"
                      value={url}
                      onChangeText={setUrl}
                      autoCapitalize="none"
                      keyboardType="url"
                    />
                    <Button
                      title="Get the transcript"
                      icon="logo-youtube"
                      kind="secondary"
                      busy={busy === "youtube"}
                      onPress={fetchTranscript}
                    />
                    <T variant="xs" tone="muted">
                      Only works on videos that have captions.
                    </T>
                  </View>
                ) : null}
              </Card>

              <Card style={{ gap: space.md }}>
                <View style={{ gap: space.sm }}>
                  <Label>How many questions</Label>
                  <SegmentedRow options={COUNTS} value={count} onChange={setCount} />
                </View>
                <Button
                  title="Make flashcards & a quiz"
                  icon="sparkles"
                  disabled={text.trim().length < 40}
                  onPress={generate}
                />
              </Card>
            </>
          ) : (
            <>
              <SegmentedRow
                options={[
                  { label: `Flashcards (${cards.length})`, value: "cards" as const },
                  { label: `Quiz (${questions.length})`, value: "quiz" as const },
                ]}
                value={tab}
                onChange={setTab}
              />

              {tab === "cards" ? (
                cards.length ? (
                  cards.map((c, i) => <Flashcard key={i} term={c.term} definition={c.definition} />)
                ) : (
                  <Notice text="No key concepts came back for this material." icon="information-circle-outline" />
                )
              ) : questions.length ? (
                questions.map((q, i) => <QuizItem key={q.id ?? i} question={q} index={i} />)
              ) : (
                <Notice text="No questions came back for this material." icon="information-circle-outline" />
              )}
            </>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}

/**
 * A card that hides its answer until asked.
 *
 * The hiding is the entire point — a "flashcard" showing both sides at
 * once is just a definition list, and recall you didn't attempt teaches
 * nothing.
 */
function Flashcard({ term, definition }: { term: string; definition: string }) {
  const { colors } = useTheme();
  const [shown, setShown] = useState(false);
  return (
    <Pressable
      onPress={() => { setShown((v) => !v); Haptics.selectionAsync().catch(() => {}); }}
      accessibilityRole="button"
      accessibilityState={{ expanded: shown }}
      accessibilityLabel={shown ? `${term}. ${definition}. Tap to hide.` : `${term}. Tap to reveal.`}
    >
      <Card style={{ gap: space.sm, minHeight: 96, justifyContent: "center" }}>
        <T variant="base" weight="700">{term}</T>
        {shown ? (
          <T variant="sm" tone="secondary">{definition}</T>
        ) : (
          <T variant="sm" tone="muted">Tap to reveal</T>
        )}
      </Card>
    </Pressable>
  );
}

/**
 * One question, answered in the student's own words.
 *
 * Marked by `/study/evaluate` rather than by string comparison, because
 * the answers here are sentences: "makes energy for the cell" and "site of
 * ATP synthesis" are the same answer, and any exact match would fail one
 * of them and teach the student to guess the wording instead of the idea.
 */
function QuizItem({ question, index }: { question: StudyQuestion; index: number }) {
  const { colors } = useTheme();
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Evaluation | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(false);

  async function submit() {
    if (!answer.trim()) return;
    setBusy(true);
    setFailed(null);
    try {
      setResult(
        await evaluateAnswer({
          question: question.question,
          correctAnswer: question.answer,
          userAnswer: answer.trim(),
        }),
      );
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
    } catch (e: any) {
      setFailed(e?.message || "Couldn't mark that answer.");
    } finally {
      setBusy(false);
    }
  }

  const tone = result?.verdict ? VERDICT_TONE[result.verdict] : null;
  const toneFg = tone === "ok" ? colors.ok : tone === "warn" ? colors.warn : colors.danger;
  const toneBg =
    tone === "ok" ? colors.okSoft : tone === "warn" ? colors.warnSoft : colors.dangerSoft;

  return (
    <Card style={{ gap: space.sm }}>
      <View style={{ flexDirection: "row", gap: space.sm, alignItems: "flex-start" }}>
        <T variant="sm" tone="muted" weight="700">{index + 1}.</T>
        <T variant="base" weight="600" style={{ flex: 1 }}>{question.question}</T>
      </View>
      {question.type ? <Chip label={String(question.type)} /> : null}

      <TextInput
        value={answer}
        onChangeText={setAnswer}
        multiline
        editable={!result}
        placeholder="Answer in your own words…"
        placeholderTextColor={colors.textMuted}
        accessibilityLabel={`Your answer to question ${index + 1}`}
        style={{
          minHeight: 72,
          borderWidth: 1,
          borderColor: colors.border,
          borderRadius: radius.md,
          padding: space.md,
          color: colors.textPrimary,
          backgroundColor: colors.bgCard,
          textAlignVertical: "top",
          opacity: result ? 0.6 : 1,
        }}
      />

      {failed ? <Notice text={failed} icon="alert-circle-outline" /> : null}

      {!result ? (
        <View style={{ flexDirection: "row", gap: space.sm }}>
          <View style={{ flex: 1 }}>
            <Button
              title="Check answer"
              kind="secondary"
              busy={busy}
              disabled={!answer.trim()}
              onPress={submit}
            />
          </View>
          <View style={{ flex: 1 }}>
            <Button
              title={revealed ? "Hide answer" : "Show answer"}
              kind="ghost"
              onPress={() => setRevealed((v) => !v)}
            />
          </View>
        </View>
      ) : null}

      {/* Shown only when asked for, and never before an attempt has been
          marked — an answer sitting on screen is one the student reads
          instead of recalling. */}
      {revealed && !result ? (
        <View style={{ gap: 4, padding: space.md, borderRadius: radius.md, backgroundColor: colors.bgElevated }}>
          <Label>Answer</Label>
          <T variant="sm" tone="secondary">{question.answer}</T>
        </View>
      ) : null}

      {result ? (
        <View
          style={{
            gap: 6,
            padding: space.md,
            borderRadius: radius.md,
            backgroundColor: toneBg,
            borderWidth: 1,
            borderColor: toneFg,
          }}
        >
          <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
            <Ionicons
              name={
                result.verdict === "correct"
                  ? "checkmark-circle"
                  : result.verdict === "partial"
                    ? "remove-circle"
                    : "close-circle"
              }
              size={20}
              color={toneFg}
            />
            <T variant="base" weight="700" style={{ color: toneFg, flex: 1 }}>
              {result.verdict === "correct"
                ? "Got it"
                : result.verdict === "partial"
                  ? "Partly there"
                  : "Not quite"}
            </T>
            {typeof result.score === "number" ? <Chip label={`${result.score}`} /> : null}
          </View>
          {result.what_was_right ? <T variant="sm">{result.what_was_right}</T> : null}
          {result.what_was_missing ? (
            <T variant="sm" tone="secondary">{result.what_was_missing}</T>
          ) : null}
          {result.memory_anchor ? (
            <T variant="sm" tone="muted">Remember it as: {result.memory_anchor}</T>
          ) : null}
          {result.better_answer ? (
            <View style={{ gap: 2, marginTop: 4 }}>
              <Label>A fuller answer</Label>
              <T variant="sm" tone="secondary">{result.better_answer}</T>
            </View>
          ) : null}
          <Button
            title="Try again"
            kind="ghost"
            onPress={() => { setResult(null); setAnswer(""); setRevealed(false); }}
          />
        </View>
      ) : null}
    </Card>
  );
}
