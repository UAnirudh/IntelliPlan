"""Tests for the Narrator — injected AI, graceful fallback."""

from __future__ import annotations

import pytest

from intelliplan.intelligence.narrator import brief, fallback_brief


FACTS = {
    "grade_level": "11th grade",
    "top_tasks": [
        {
            "title": "Chem practice packet",
            "course": "AP Chemistry",
            "due_date": "2026-06-13",
            "est_minutes": 45,
            "priority_tier": "critical",
            "top_reason": "Due tomorrow",
        }
    ],
    "task_count": 3,
    "total_remaining_minutes": 180,
    "heaviest_day_name": "Wednesday",
    "workload_summary": "Wednesday is the heaviest day this week.",
    "health_score": 78,
    "health_delta": -4,
    "health_summary": "Health dropped 4 points — 1 overdue item.",
}


class TestAIPath:
    def test_uses_injected_ai(self) -> None:
        calls: list = []

        def fake_ai(messages, **kwargs):
            calls.append((messages, kwargs))
            return {"headline": "Chem first.", "body": "Packet tonight.", "tone": "calm-focused"}

        out = brief(FACTS, student_name="Anirudh Ulabala", ai_chat_json=fake_ai)
        assert out.headline == "Chem first."
        assert out.body == "Packet tonight."
        assert out.tone == "calm-focused"
        assert len(calls) == 1
        # The student's first name is injected into the facts payload
        assert "Anirudh" in calls[0][0][1]["content"]

    def test_invalid_tone_normalised(self) -> None:
        def fake_ai(messages, **kwargs):
            return {"headline": "H", "body": "B", "tone": "dramatic"}

        assert brief(FACTS, ai_chat_json=fake_ai).tone == "calm-focused"

    def test_missing_fields_falls_back(self) -> None:
        def fake_ai(messages, **kwargs):
            return {"headline": "", "body": ""}

        out = brief(FACTS, student_name="Anirudh", ai_chat_json=fake_ai)
        assert out.generated_by == "fallback-template"

    def test_ai_exception_falls_back(self) -> None:
        def fake_ai(messages, **kwargs):
            raise RuntimeError("provider down")

        out = brief(FACTS, student_name="Anirudh", ai_chat_json=fake_ai)
        assert out.generated_by == "fallback-template"
        assert out.headline  # never blank


class TestFallback:
    def test_names_top_task(self) -> None:
        out = fallback_brief(FACTS, student_name="Anirudh")
        assert "Chem practice packet" in out.headline

    def test_mentions_heaviest_day(self) -> None:
        out = fallback_brief(FACTS, student_name="Anirudh")
        assert "Wednesday" in out.body

    def test_negative_health_sets_heads_up(self) -> None:
        assert fallback_brief(FACTS).tone == "heads-up"

    def test_empty_plan_celebrates(self) -> None:
        facts = dict(FACTS, top_tasks=[], health_delta=0)
        out = fallback_brief(facts, student_name="Anirudh")
        assert "caught up" in out.headline.lower()
        assert out.tone == "encouraging"

    def test_no_name_uses_generic(self) -> None:
        facts = dict(FACTS, top_tasks=[], health_delta=0)
        out = fallback_brief(facts, student_name="")
        assert "there" in out.headline
