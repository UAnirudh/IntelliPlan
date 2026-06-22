"""Abstract base classes for LMS providers.

A provider's job: turn provider-specific assignment shapes into the
shared :class:`LMSAssignment` so the sync layer can persist them with
the same code path regardless of source.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class LMSCourse:
    """Normalized course record. ``external_id`` is provider-namespaced
    (e.g. ``"google_classroom:abc123"``) so two providers with the same
    course code never collide."""

    external_id: str
    name: str
    section: str | None = None
    teacher_name: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LMSAssignment:
    """Normalized assignment record."""

    external_id: str
    course_external_id: str
    title: str
    due_at: datetime | None
    description: str | None = None
    points_possible: float | None = None
    url: str | None = None
    submitted: bool = False
    graded: bool = False
    score: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class LMSProvider(ABC):
    """Provider contract. Subclasses MUST set ``key`` and ``display_name``."""

    key: str = ""
    display_name: str = ""
    docs_url: str = ""

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """True when OAuth credentials / API keys are present in env."""

    @abstractmethod
    def get_authorize_url(self, *, user_id: int, redirect_uri: str, state: str) -> str:
        """Return the URL to redirect the user to for OAuth consent."""

    @abstractmethod
    def exchange_code(self, *, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange an OAuth code for tokens. Returns ``{access_token, refresh_token, expires_at}``."""

    @abstractmethod
    def list_courses(self, *, tokens: dict[str, Any]) -> list[LMSCourse]:
        ...

    @abstractmethod
    def list_assignments(self, *, tokens: dict[str, Any], course: LMSCourse) -> list[LMSAssignment]:
        ...

    def refresh_tokens(self, *, tokens: dict[str, Any]) -> dict[str, Any]:
        """Optional: refresh access tokens. Default: no-op (return same tokens)."""
        return tokens

    def sync_all(self, *, tokens: dict[str, Any]) -> list[LMSAssignment]:
        """Convenience: fan-out list_courses + list_assignments."""
        out: list[LMSAssignment] = []
        for course in self.list_courses(tokens=tokens):
            try:
                out.extend(self.list_assignments(tokens=tokens, course=course))
            except Exception:
                continue
        return out


class StubProvider(LMSProvider):
    """Base for providers that aren't yet credentialed. Returns empty
    results and raises clearly on actions that need real OAuth."""

    @classmethod
    def is_configured(cls) -> bool:
        return False

    def get_authorize_url(self, *, user_id: int, redirect_uri: str, state: str) -> str:
        raise NotImplementedError(
            f"{self.display_name} OAuth credentials are not configured. "
            f"Set the required env vars to enable this integration."
        )

    def exchange_code(self, *, code: str, redirect_uri: str) -> dict[str, Any]:
        raise NotImplementedError(f"{self.display_name} is not configured.")

    def list_courses(self, *, tokens: dict[str, Any]) -> list[LMSCourse]:
        return []

    def list_assignments(self, *, tokens: dict[str, Any], course: LMSCourse) -> list[LMSAssignment]:
        return []
