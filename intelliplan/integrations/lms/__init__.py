"""LMS / SIS integration framework.

Each provider exposes a uniform :class:`LMSProvider` interface so the
rest of the app can fan-out assignment sync without caring which LMS
the student is connected to.

Providers ship as stubs until OAuth credentials are configured in env
vars. The `is_configured()` classmethod gates the UI — "Connect" buttons
only render for providers that have credentials.
"""

from intelliplan.integrations.lms.base import LMSProvider, LMSAssignment, LMSCourse
from intelliplan.integrations.lms.registry import (
    get_provider,
    available_providers,
    configured_providers,
)

__all__ = [
    "LMSProvider",
    "LMSAssignment",
    "LMSCourse",
    "get_provider",
    "available_providers",
    "configured_providers",
]
