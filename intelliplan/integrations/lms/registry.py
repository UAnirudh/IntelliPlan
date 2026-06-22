"""Provider registry — maps the public ``key`` to a provider instance."""

from __future__ import annotations

from intelliplan.integrations.lms.base import LMSProvider
from intelliplan.integrations.lms.blackboard import BlackboardProvider
from intelliplan.integrations.lms.google_classroom import GoogleClassroomProvider
from intelliplan.integrations.lms.moodle import MoodleProvider
from intelliplan.integrations.lms.powerschool import PowerSchoolProvider


_PROVIDERS: dict[str, type[LMSProvider]] = {
    GoogleClassroomProvider.key: GoogleClassroomProvider,
    BlackboardProvider.key: BlackboardProvider,
    MoodleProvider.key: MoodleProvider,
    PowerSchoolProvider.key: PowerSchoolProvider,
}


def get_provider(key: str) -> LMSProvider | None:
    cls = _PROVIDERS.get(key)
    return cls() if cls else None


def available_providers() -> list[dict[str, object]]:
    """Lightweight provider list for UI rendering."""
    return [
        {
            "key": cls.key,
            "display_name": cls.display_name,
            "docs_url": cls.docs_url,
            "configured": cls.is_configured(),
        }
        for cls in _PROVIDERS.values()
    ]


def configured_providers() -> list[type[LMSProvider]]:
    return [cls for cls in _PROVIDERS.values() if cls.is_configured()]
