"""LLM analysis passes that keep the student model current.

Port of the adaptive-ai-tutor ``src/lib/tutor/gemini.ts`` analysis calls, run
through IntelliPlan's own provider stack (``ai_provider.chat``, which already
routes Gemini -> Groq -> Ollama). Every function degrades to a safe fallback
rather than raising: a failed analysis must never break a tutor reply.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Transcript slices handed to each analysis pass.
_SUMMARY_TURNS = 40
_MEMORY_TURNS = 12
_MAX_IMPORTED_CHARS = 5000

FALLBACK_LEARNER_MEMORY: dict[str, Any] = {
    'learnerType': 'adaptive mixed learner',
    'confidence': 0.35,
    'summary': (
        'Not enough evidence yet for a precise learner model. Keep collecting '
        'sessions and imported context.'
    ),
    'strengths': [],
    'frictionPoints': [],
    'preferredPatterns': [],
    'recommendedStrategies': [
        'Ask short diagnostic questions before long explanations.',
        'Reflect back uncertainty and adjust difficulty after each answer.',
    ],
    'learnerSignals': {},
}

EMPTY_SUMMARY: dict[str, Any] = {
    'summaryText': 'Session completed.',
    'topicsCovered': [],
    'understood': [],
    'struggled': [],
    'reviewNext': [],
}


def _clean_json(text: str) -> str:
    text = (text or '').strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[-1]
        text = text.rsplit('```', 1)[0]
    return text.strip()


def _parse_json(text: str, fallback: Any) -> Any:
    try:
        return json.loads(_clean_json(text))
    except (TypeError, ValueError):
        return fallback


def _transcript(messages: list[dict[str, Any]], limit: int) -> str:
    lines = []
    for message in (messages or [])[-limit:]:
        speaker = 'Student' if message.get('role') == 'user' else 'Tutor'
        lines.append(f"{speaker}: {str(message.get('content') or '').strip()}")
    return '\n\n'.join(lines)


def _string_list(value: Any, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text[:300])
    return out[:limit]


def generate_session_summary(chat: Callable[..., str],
                             messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a finished tutoring conversation."""
    transcript = _transcript(messages, _SUMMARY_TURNS)
    if not transcript:
        return dict(EMPTY_SUMMARY)

    prompt = f"""Analyze this tutoring session transcript and return a JSON object with these fields:
- summaryText: a 2-3 sentence summary of what was covered
- topicsCovered: array of specific topics discussed
- understood: array of concepts the student demonstrated understanding of
- struggled: array of concepts the student had difficulty with
- reviewNext: array of topics to review in the next session

Transcript:
{transcript}

Return ONLY valid JSON, no markdown fences."""

    try:
        raw = chat(
            model='openai/gpt-oss-120b',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.3,
            max_tokens=1024,
            response_format={'type': 'json_object'},
        )
    except Exception as exc:
        logger.warning('adaptive tutor: session summary failed: %s', exc)
        return dict(EMPTY_SUMMARY)

    parsed = _parse_json(raw, None)
    if not isinstance(parsed, dict):
        return dict(EMPTY_SUMMARY)

    return {
        'summaryText': str(parsed.get('summaryText') or EMPTY_SUMMARY['summaryText'])[:4000],
        'topicsCovered': _string_list(parsed.get('topicsCovered'), 20),
        'understood': _string_list(parsed.get('understood'), 20),
        'struggled': _string_list(parsed.get('struggled'), 20),
        'reviewNext': _string_list(parsed.get('reviewNext'), 20),
    }


def extract_mistakes(chat: Callable[..., str],
                     messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Pull the student's misconceptions out of a transcript."""
    transcript = _transcript(messages, _SUMMARY_TURNS)
    if not transcript:
        return []

    prompt = f"""Analyze this tutoring session and identify mistakes or misconceptions the student demonstrated.

Return a JSON object of the form {{"mistakes": [...]}} where each entry has:
- subject: the broad subject area (e.g. "Mathematics", "Physics")
- topic: the specific topic (e.g. "Quadratic Equations", "Newton's Laws")
- mistakeType: a short label (e.g. "sign error", "conceptual confusion", "formula misapplication")
- description: a brief description of what the student got wrong

Only report mistakes the STUDENT made. If there were none, return {{"mistakes": []}}.

Transcript:
{transcript}

Return ONLY valid JSON, no markdown fences."""

    try:
        raw = chat(
            model='openai/gpt-oss-120b',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.3,
            max_tokens=1024,
            response_format={'type': 'json_object'},
        )
    except Exception as exc:
        logger.warning('adaptive tutor: mistake extraction failed: %s', exc)
        return []

    parsed = _parse_json(raw, None)
    if isinstance(parsed, dict):
        parsed = parsed.get('mistakes')
    if not isinstance(parsed, list):
        return []

    mistakes = []
    for item in parsed[:10]:
        if not isinstance(item, dict):
            continue
        description = str(item.get('description') or '').strip()
        if not description:
            continue
        mistakes.append({
            'subject': str(item.get('subject') or 'General').strip()[:120],
            'topic': str(item.get('topic') or '').strip()[:200],
            'mistakeType': str(item.get('mistakeType') or 'misconception').strip()[:160],
            'description': description[:2000],
        })
    return mistakes


def analyze_learner_memory(chat: Callable[..., str], *,
                           existing_summary: str | None = None,
                           profile: dict[str, Any] | None = None,
                           imported_memories: list[dict[str, Any]] | None = None,
                           recent_transcript: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Rebuild the durable learner model from every available signal."""
    imported_blocks = []
    for memory in (imported_memories or [])[:8]:
        compact = ' '.join(str(memory.get('text') or '').split())[:_MAX_IMPORTED_CHARS]
        imported_blocks.append(
            f"Provider: {memory.get('provider')}\n"
            f"Label: {memory.get('source_label') or 'import'}\n"
            f"Text: {compact}"
        )
    imported = '\n\n---\n\n'.join(imported_blocks)
    transcript = _transcript(recent_transcript or [], _MEMORY_TURNS)
    profile_json = json.dumps(profile, indent=2, default=str) if profile else 'No structured profile yet.'

    prompt = f"""Build a durable learner memory for an adaptive AI tutor.

Use the structured profile, imported AI-provider chat logs/memory, the existing learner summary, and the latest tutor transcript. Infer how this person learns, what explanations help, what causes friction, and what the tutor should remember in future sessions.

Return ONLY valid JSON with this exact shape:
{{
  "learnerType": "short useful label",
  "confidence": 0.0,
  "summary": "concise durable memory paragraph",
  "strengths": ["specific learning strengths"],
  "frictionPoints": ["specific recurring difficulties or blockers"],
  "preferredPatterns": ["ways explanations should be shaped"],
  "recommendedStrategies": ["actions the tutor should take"],
  "learnerSignals": {{
    "pace": "string",
    "motivation": "string",
    "bestExamples": ["string"],
    "avoid": ["string"]
  }},
  "modalityScores": {{
    "auditory": 0.0,
    "visual": 0.0,
    "reading": 0.0,
    "reasoning": "brief explanation of modality detection"
  }}
}}

For modalityScores, assess the learner's preferred modality (scores must sum to 1.0):
- auditory: prefers listening and verbal explanations, talks through problems, asks to be "walked through" things
- visual: prefers diagrams, charts, interactive examples, asks for visualizations and step-by-step visual breakdowns
- reading: prefers written text and detailed written explanations, reads carefully, asks follow-ups about the text

Rules:
- Do not invent private facts that the evidence does not support.
- Prefer stable learning traits over one-off mood or wording.
- If evidence is weak, lower confidence and say what to observe next.
- Keep arrays to at most 8 items each.

Existing learner summary:
{existing_summary or 'None yet.'}

Structured profile:
{profile_json}

Imported AI-provider memories:
{imported or 'No imported memory provided.'}

Latest tutor transcript:
{transcript or 'No latest tutor transcript provided.'}"""

    try:
        raw = chat(
            model='openai/gpt-oss-120b',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.2,
            max_tokens=1600,
            response_format={'type': 'json_object'},
        )
    except Exception as exc:
        logger.warning('adaptive tutor: learner memory analysis failed: %s', exc)
        return dict(FALLBACK_LEARNER_MEMORY)

    parsed = _parse_json(raw, None)
    if not isinstance(parsed, dict):
        return dict(FALLBACK_LEARNER_MEMORY)

    try:
        confidence = min(1.0, max(0.0, float(parsed.get('confidence'))))
    except (TypeError, ValueError):
        confidence = FALLBACK_LEARNER_MEMORY['confidence']

    signals = parsed.get('learnerSignals')
    modality = parsed.get('modalityScores')

    return {
        'learnerType': str(parsed.get('learnerType') or FALLBACK_LEARNER_MEMORY['learnerType'])[:160],
        'confidence': confidence,
        'summary': str(parsed.get('summary') or FALLBACK_LEARNER_MEMORY['summary'])[:4000],
        'strengths': _string_list(parsed.get('strengths')),
        'frictionPoints': _string_list(parsed.get('frictionPoints')),
        'preferredPatterns': _string_list(parsed.get('preferredPatterns')),
        'recommendedStrategies': (
            _string_list(parsed.get('recommendedStrategies'))
            or FALLBACK_LEARNER_MEMORY['recommendedStrategies']
        ),
        'learnerSignals': signals if isinstance(signals, dict) else {},
        'modalityScores': modality if isinstance(modality, dict) else None,
    }
