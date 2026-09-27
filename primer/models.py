"""Stable curriculum value types shared by fixed and generated content."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    id: str
    domain: str
    title: str
    prerequisite: str | None = None
    grade: int = 0


@dataclass(frozen=True)
class Item:
    id: str
    skill_id: str
    prompt: str
    answer: str
    options: tuple[str, ...] = ()
    hint: str = ''
    explanation: str = ''
