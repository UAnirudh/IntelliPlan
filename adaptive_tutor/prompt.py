"""Context-aware system prompt assembly for the adaptive Plani tutor.

Port of the adaptive-ai-tutor ``src/lib/tutor/prompt-builder.ts``. Every turn
the full student model is flattened into an additional system message that
sits alongside IntelliPlan's existing ``TUTOR_SYSTEM_PROMPT``.
"""

from __future__ import annotations

from typing import Any

STYLE_MAP = {
    'concise': 'Be concise and direct. Skip unnecessary filler.',
    'balanced': 'Provide clear explanations with moderate detail. Balance depth with brevity.',
    'detailed': 'Give thorough, detailed explanations. Include background context and reasoning.',
}

LENGTH_MAP = {
    'short': 'Keep responses short - 2-4 sentences for simple concepts.',
    'medium': 'Use moderate response length - a few paragraphs when needed.',
    'long': 'Feel free to write longer explanations with full step-by-step breakdowns.',
}

DIFFICULTY_MAP = {
    'easy': 'Start with fundamentals. Use simple language and lots of examples.',
    'medium': 'Assume some baseline knowledge. Build on what the student knows.',
    'hard': 'Challenge the student. Introduce edge cases and deeper reasoning.',
    'adaptive': 'Adapt difficulty based on how the student responds. Start moderate and adjust.',
}

#: Mastery above this reads as strong; below the second value it needs work.
_STRONG_MASTERY = 80
_DEVELOPING_MASTERY = 50


def _mastery_level(score: float) -> str:
    if score >= _STRONG_MASTERY:
        return 'strong'
    if score >= _DEVELOPING_MASTERY:
        return 'developing'
    return 'needs work'


def _profile_section(profile: dict[str, Any]) -> list[str]:
    lines = ['\n## Student Profile']
    lines.append(f"- Grade Level: {profile.get('grade_level') or 'Not specified'}")
    subjects = profile.get('subjects') or []
    lines.append(f"- Subjects: {', '.join(subjects) if subjects else 'Not specified'}")
    lines.append(f"- Short-term Goals: {profile.get('short_term_goals') or 'Not specified'}")
    lines.append(f"- Long-term Goals: {profile.get('long_term_goals') or 'Not specified'}")

    interests = profile.get('interests') or []
    if interests:
        lines.append(f"- Interests & Hobbies: {', '.join(interests)}")
        lines.append('  -> Use these interests in examples and analogies when relevant.')

    lines.append('\n## Response Style Preferences')
    lines.append(f"- Style: {STYLE_MAP.get(profile.get('explanation_style'), STYLE_MAP['balanced'])}")
    lines.append(f"- Length: {LENGTH_MAP.get(profile.get('explanation_length'), LENGTH_MAP['medium'])}")
    lines.append(f"- Difficulty: {DIFFICULTY_MAP.get(profile.get('difficulty_level'), DIFFICULTY_MAP['medium'])}")
    return lines


def _learner_memory_section(memory: dict[str, Any]) -> list[str]:
    lines = ['\n## Durable Learner Memory']
    lines.append(f"- Learner Type: {memory.get('learner_type') or 'Still learning'}")
    lines.append(f"- Confidence: {round(float(memory.get('confidence') or 0) * 100)}%")
    if memory.get('summary'):
        lines.append(f"- Memory Summary: {memory['summary']}")
    for label, key in (
        ('Strengths', 'strengths'),
        ('Friction Points', 'friction_points'),
        ('Preferred Explanation Patterns', 'preferred_patterns'),
        ('Recommended Tutor Strategies', 'recommended_strategies'),
    ):
        values = memory.get(key) or []
        if values:
            lines.append(f"- {label}: {'; '.join(values)}")
    lines.append(
        '  -> Treat this as durable memory. Use it to choose examples, pacing, '
        'checks for understanding, and how much scaffolding to provide.'
    )
    return lines


def _mastery_section(mastery: list[dict[str, Any]]) -> list[str]:
    lines = ['\n## Subject Mastery']
    for row in sorted(mastery, key=lambda r: float(r.get('mastery_score') or 0), reverse=True)[:15]:
        score = float(row.get('mastery_score') or 0)
        confidence = float(row.get('confidence_level') or 0)
        lines.append(
            f"- {row.get('subject')} > {row.get('topic')}: {round(score)}% "
            f"({_mastery_level(score)}, confidence: {round(confidence)}%)"
        )
    lines.append('  -> For weak topics, provide more scaffolding. For strong topics, increase challenge.')
    return lines


def _mistakes_section(mistakes: list[dict[str, Any]]) -> list[str]:
    lines = ['\n## Recurring Mistakes']
    for row in mistakes[:10]:
        lines.append(
            f"- {row.get('subject')} > {row.get('topic')}: \"{row.get('mistake_type')}\" - "
            f"{row.get('description')} (seen {row.get('frequency')}x)"
        )
    lines.append(
        '  -> Watch for these patterns. If the student makes a similar mistake, '
        'address it directly and review the prerequisites.'
    )
    return lines


def _sessions_section(sessions: list[dict[str, Any]]) -> list[str]:
    lines = ['\n## Recent Session Context']
    for row in sessions[:3]:
        if row.get('summary_text'):
            started = row.get('started_at')
            stamp = started.strftime('%Y-%m-%d') if hasattr(started, 'strftime') else 'recent'
            lines.append(f"- Session ({stamp}): {row['summary_text']}")
        if row.get('struggled'):
            lines.append(f"  Struggled with: {', '.join(row['struggled'])}")
        if row.get('review_next'):
            lines.append(f"  Should review: {', '.join(row['review_next'])}")
    return lines


def _voice_section() -> list[str]:
    return [
        "This student's reply will be read aloud. Optimize for spoken delivery:",
        '- Write in a conversational, spoken tone, as if talking directly to the student.',
        '- Use short sentences. Avoid walls of text.',
        '- Spell out symbols that sound awkward when read ("equals" not "=", "times" not "x").',
        '- Use natural pauses with commas and periods.',
        '- For math, write it verbally: "x squared plus 3x minus 7".',
        '- Still include artifacts for quizzes and visuals - those render visually alongside the audio.',
    ]


def _artifact_section(use_voice: bool) -> list[str]:
    lines = ['\n## Interactive Artifacts']
    lines.append(
        'You can create interactive content that renders in the student\'s browser. '
        'Use artifacts for quizzes, visualizations, interactive diagrams, and practice exercises.'
    )
    lines.append('To create an artifact, use this exact format:')
    lines.append('```')
    lines.append(':::artifact{type="quiz" title="Quick Check: Topic Name"}')
    lines.append('<h2>Question text</h2>')
    lines.append('<div id="quiz"><!-- HTML + JS content --></div>')
    lines.append(':::')
    lines.append('```')
    lines.append(
        'Artifact types: "quiz" for practice questions, "visualization" for charts/diagrams, '
        '"html" for interactive exercises, "code" for runnable examples.'
    )
    lines.append(
        'Artifacts are sandboxed HTML. Inline <script> and <style> tags work. '
        'The sandbox ships these CSS classes:'
    )
    lines.append('- .quiz-option - clickable answer buttons (add .correct or .incorrect on click)')
    lines.append('- .feedback.correct / .feedback.incorrect - result messages')
    lines.append('- .card - content card')
    lines.append('- .progress-bar + .progress-fill - progress indicators')
    lines.append('- .chart-container - for canvas/svg visualizations')
    lines.append('- Standard elements (button, input, select, table, canvas) are styled automatically.')
    lines.append('When to use artifacts:')
    lines.append('- The student asks to be quizzed or tested -> build an interactive quiz')
    lines.append('- Explaining data, comparisons, or processes -> build a visualization')
    lines.append('- The student needs practice -> build an interactive exercise')
    lines.append('- Step-by-step walkthroughs -> build an interactive guide')
    if use_voice:
        lines.append(
            'This student is in voice mode, so lean on artifacts more - they hear your '
            'words and see the artifact at the same time. Use both channels.'
        )
    lines.append('Keep artifacts focused and self-contained. Always include explanatory text around them.')
    return lines


def build_adaptive_prompt(context: dict[str, Any], use_voice: bool = False,
                          use_artifacts: bool = True) -> str:
    """Flatten the student model into one system message."""
    profile = context.get('profile') or {}
    mastery = context.get('mastery') or []
    mistakes = context.get('mistakes') or []
    sessions = context.get('recent_sessions') or []
    memory = context.get('learner_memory')
    imports = context.get('memory_imports') or []

    sections: list[str] = [
        'ADAPTIVE STUDENT MODEL. Everything below is what IntelliPlan has learned '
        'about this specific student. Personalize every response to it. Never read '
        'the model back to the student verbatim - let it shape the answer.'
    ]

    sections.extend(_profile_section(profile))

    if memory:
        sections.extend(_learner_memory_section(memory))

    if imports:
        sections.append('\n## Imported AI Memory Sources')
        for item in imports[:6]:
            label = f" ({item['source_label']})" if item.get('source_label') else ''
            body = item.get('extracted_summary') or str(item.get('raw_text') or '')[:240]
            sections.append(f"- {item.get('provider')}{label}: {body}")

    if mastery:
        sections.extend(_mastery_section(mastery))

    if mistakes:
        sections.extend(_mistakes_section(mistakes))

    if sessions:
        sections.extend(_sessions_section(sessions))

    sections.append('\n## Learning Modality')
    if use_voice:
        sections.extend(_voice_section())
    else:
        sections.append(
            'This reply is read on screen. Use normal written formatting, '
            'and keep math in plain text (the chat does not render LaTeX).'
        )

    sections.append('\n## Adaptive Behavior')
    sections.append('- Explain clearly and check understanding frequently.')
    sections.append('- Ask follow-up questions to verify comprehension.')
    sections.append("- Give concrete examples, using the student's interests where they fit.")
    sections.append('- If the student seems confused, slow down and try a different approach.')
    sections.append('- If the student is doing well, gradually increase complexity.')
    sections.append(
        '- Update your behavior as new evidence appears. The promise is memory: remember '
        'patterns, avoid repeating failed approaches, and make continuity obvious.'
    )
    sections.append('- After explaining a concept, offer a quick practice question.')
    sections.append('- Never be condescending. Be encouraging but honest about mistakes.')

    if use_artifacts:
        sections.extend(_artifact_section(use_voice))

    return '\n'.join(sections)
