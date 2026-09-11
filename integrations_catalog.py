"""One description of every integration IntelliPlan offers.

Why this exists
---------------
The same list was written out by hand in four places -- the dashboard's
Integrations modal, the Settings page, /connect, and the login pages -- and
they had drifted badly. The modal listed two integrations of ten. Settings
listed five. The calendar-feed path, which is the only one that needs
nothing from a school admin, appeared on exactly one page.

That drift is not cosmetic: a student who opens Integrations and sees two
rows concludes IntelliPlan connects to two things. The fix is one catalogue
that every surface renders, so adding an integration is one entry rather
than four edits and three omissions.

What a connection method records
--------------------------------
Not just how to start it, but what it *costs the student* and whether it
needs somebody else. Those are the facts that decide whether a student
finishes connecting, and they were the facts the UI never showed. A method
that needs a school admin can be unavailable through no fault of theirs,
and telling them that up front is the difference between "IntelliPlan is
broken" and "my school hasn't done this yet".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

#: How much work the student has to do. Used to order methods so the
#: cheapest route is the one they see first.
FRICTION_ORDER = {"instant": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class Method:
    """One way to connect an integration."""

    key: str
    label: str
    #: One line a student can act on, in their words, not ours.
    how: str
    #: "instant" (one click), "low" (paste one thing), "medium" (a few
    #: steps in a settings page), "high" (multiple steps plus a key).
    friction: str = "low"
    #: True when this cannot work until someone at the school does
    #: something. The single most useful fact to show and the one most
    #: often omitted.
    needs_school_admin: bool = False
    #: Where the UI should send them. None when the UI runs its own flow.
    start_url: Optional[str] = None
    #: POST rather than a link.
    start_post: Optional[str] = None
    note: str = ""


@dataclass(frozen=True)
class Integration:
    id: str
    name: str
    #: "lms" (brings in coursework) or "productivity" (sends work out).
    category: str
    #: What the student gets. Written as a promise we actually keep.
    brings: str
    methods: tuple = field(default_factory=tuple)
    status_url: Optional[str] = None
    disconnect_url: Optional[str] = None
    #: Set when the integration is not live yet, so the UI can say so
    #: rather than offering a button that fails.
    coming_soon: bool = False
    #: True when a feed or export carries no grades. Stated up front so a
    #: student does not conclude the gradebook is broken.
    no_grades: bool = False

    def sorted_methods(self):
        """Cheapest first -- but a method nobody at the school has enabled
        is not cheap.

        Ordering on friction alone put "Sign in with Canvas" at the top,
        because one click beats pasting a link. It only beats it where the
        school has issued a Developer Key, and most have not: the student
        clicks the best-looking option and is told it is not configured.
        So anything gated on a school admin sorts below everything that
        works today, however many steps it takes.
        """
        return tuple(sorted(
            self.methods,
            key=lambda m: (m.needs_school_admin, FRICTION_ORDER.get(m.friction, 9)),
        ))


CANVAS = Integration(
    id="canvas",
    name="Canvas LMS",
    category="lms",
    brings="Assignments, due dates, courses and grades",
    status_url="/api/integrations/status",
    methods=(
        Method(
            key="calendar_feed",
            label="Calendar link",
            how="Canvas → Calendar → Calendar Feed, then copy the link.",
            friction="low",
            start_url="/login/canvas#calendar-feed",
            note="Fastest route. Brings due dates, not grades.",
        ),
        Method(
            key="oauth",
            label="Sign in with Canvas",
            how="One click, if your school has registered IntelliPlan.",
            friction="instant",
            needs_school_admin=True,
            start_url="/oauth/canvas",
            note="Canvas requires each school to issue its own Developer "
                 "Key, so this only works where an admin has done that.",
        ),
        Method(
            key="token",
            label="Access token",
            how="Canvas → Account → Settings → New Access Token, then paste "
                "it with your school's Canvas address.",
            friction="medium",
            start_url="/login/canvas",
            note="Works on every Canvas and brings grades too.",
        ),
    ),
)

STUDENTVUE = Integration(
    id="studentvue",
    name="StudentVue",
    category="lms",
    brings="Assignments, grades and missing work",
    methods=(
        Method(
            key="credentials",
            label="District sign-in",
            how="Your district's StudentVue address plus the username and "
                "password you already use.",
            friction="low",
            start_url="/login/studentvue",
        ),
    ),
)

SCHOOLOGY = Integration(
    id="schoology",
    name="Schoology",
    category="lms",
    brings="Assignments and due dates",
    methods=(
        Method(
            key="api_key",
            label="API key and secret",
            how="Schoology → Settings → API Credentials.",
            friction="high",
            start_url="/login/schoology",
            note="Schoology hides these for many student accounts, so this "
                 "often cannot be completed. A calendar link is the more "
                 "reliable route.",
        ),
    ),
)

GOOGLE_CLASSROOM = Integration(
    id="google_classroom",
    name="Google Classroom",
    category="lms",
    brings="Courses and coursework",
    status_url="/api/lms/status/google_classroom",
    disconnect_url="/api/lms/disconnect/google_classroom",
    methods=(
        Method(
            key="oauth",
            label="Sign in with Google",
            how="Use the Google account your school gave you.",
            friction="instant",
            start_post="/api/lms/connect/google_classroom",
        ),
    ),
)

BLACKBOARD = Integration(
    id="blackboard",
    name="Blackboard Learn",
    category="lms",
    brings="Coursework and due dates",
    status_url="/api/lms/status/blackboard",
    disconnect_url="/api/lms/disconnect/blackboard",
    methods=(
        Method(
            key="oauth",
            label="Sign in through your school",
            how="Paste your school's Blackboard address, then sign in there.",
            friction="low",
            needs_school_admin=True,
            start_post="/api/lms/connect/blackboard",
            note="Your school has to approve IntelliPlan's application key "
                 "first. We check before sending you over, so you will not "
                 "land on a Blackboard error page.",
        ),
    ),
)

MOODLE = Integration(
    id="moodle",
    name="Moodle",
    category="lms",
    brings="Assignments and due dates",
    status_url="/api/lms/status/moodle",
    disconnect_url="/api/lms/disconnect/moodle",
    methods=(
        Method(
            key="token",
            label="Site address and token",
            how="Moodle → Preferences → Security keys, then paste the key "
                "with your Moodle address.",
            friction="medium",
            start_post="/api/lms/connect/moodle",
        ),
    ),
)

BRIGHTSPACE = Integration(
    id="brightspace",
    name="D2L Brightspace",
    category="lms",
    brings="Assignments and due dates",
    coming_soon=True,
    methods=(
        Method(
            key="oauth",
            label="Sign in with Brightspace",
            how="Not available yet — join the waitlist and we will email you.",
            friction="instant",
            start_post="/api/lms/connect/brightspace",
        ),
    ),
)

CALENDAR_FEED = Integration(
    id="calendar_feed",
    name="Calendar link",
    category="lms",
    brings="Everything due, from any school platform",
    no_grades=True,
    methods=(
        Method(
            key="ics",
            label="Paste a calendar link",
            how="Find the calendar feed or subscribe link in your school "
                "platform and paste it.",
            friction="low",
            start_url="/login/canvas#calendar-feed",
            note="Works on Canvas, Blackboard, Moodle and Google Calendar. "
                 "Needs nothing from your school.",
        ),
    ),
)

GOOGLE_CALENDAR = Integration(
    id="google_calendar",
    name="Google Calendar",
    category="productivity",
    brings="Plans around what is already on your calendar, and writes your "
           "study blocks back to it",
    status_url="/gcal/status",
    disconnect_url="/gcal/remove",
    methods=(
        Method(
            key="oauth",
            label="Sign in with Google",
            how="One click.",
            friction="instant",
            start_url="/oauth/google",
        ),
    ),
)

NOTION = Integration(
    id="notion",
    name="Notion",
    category="productivity",
    brings="Sends your tasks into a Notion database",
    status_url="/notion/status",
    disconnect_url="/notion/disconnect",
    methods=(
        Method(
            key="oauth",
            label="Connect with Notion",
            how="One click, then pick a database.",
            friction="instant",
            start_url="/oauth/notion",
        ),
        Method(
            key="token",
            label="Integration token",
            how="notion.so/my-integrations → New integration, then paste the "
                "secret and share a database with it.",
            friction="medium",
            start_post="/notion/connect",
        ),
    ),
)


#: Order matters: this is the order every surface renders. Coursework
#: first, because an empty planner is the problem a student came to solve.
CATALOG = (
    CANVAS,
    CALENDAR_FEED,
    GOOGLE_CLASSROOM,
    STUDENTVUE,
    BLACKBOARD,
    MOODLE,
    SCHOOLOGY,
    BRIGHTSPACE,
    GOOGLE_CALENDAR,
    NOTION,
)

BY_ID = {i.id: i for i in CATALOG}


def method_payload(m: Method) -> dict:
    return {
        "key": m.key,
        "label": m.label,
        "how": m.how,
        "friction": m.friction,
        "needs_school_admin": m.needs_school_admin,
        "start_url": m.start_url,
        "start_post": m.start_post,
        "note": m.note,
    }


def payload(integration: Integration, connected=False, detail="") -> dict:
    return {
        "id": integration.id,
        "name": integration.name,
        "category": integration.category,
        "brings": integration.brings,
        "coming_soon": integration.coming_soon,
        "no_grades": integration.no_grades,
        "connected": bool(connected),
        "detail": detail or "",
        "disconnect_url": integration.disconnect_url,
        "methods": [method_payload(m) for m in integration.sorted_methods()],
    }


def catalog_payload(connected_ids=(), details=None) -> list:
    """The whole catalogue, marked with what this user has connected."""
    connected_ids = set(connected_ids or ())
    details = details or {}
    return [
        payload(i, connected=i.id in connected_ids, detail=details.get(i.id, ""))
        for i in CATALOG
    ]
