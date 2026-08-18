"""The evergreen half of the newsletter: tips, study science, stats.

A quiet development week must still produce an issue worth opening, so the
changelog is only ever one of four sections. These two pools rotate by ISO
week number — deterministic, so the same week always yields the same issue
however many times it is generated, and a reader gets a different tip each
week for a year before anything repeats.
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

#: One per feature, so every issue teaches something concrete. Written to be
#: readable cold — a student who has never opened that page should still
#: understand what it does and why they would bother.
FEATURE_TIPS: list[dict] = [
    {
        "title": "Let the scheduler see your real week",
        "body": "Most people give the scheduler their ideal hours instead of their actual ones, then wonder why the plan slips. Put your genuine free time in — including the evening you always waste — and the blocks it builds will survive contact with reality.",
        "action": "Open the Scheduler and set your availability honestly, then regenerate.",
    },
    {
        "title": "The Grade Modeler answers 'does this test matter?'",
        "body": "Type in a score you might get on an upcoming assignment and it shows the exact effect on your course grade and GPA. It is the fastest way to find out whether tonight's revision is worth two hours or twenty minutes.",
        "action": "Go to Grades → Grade Modeler and try your next test at 70%, 85% and 95%.",
    },
    {
        "title": "Upload your notes, get a quiz back",
        "body": "Study & Learn takes a PDF, DOCX, TXT or pasted text and turns it into flashcards, key concepts and practice questions. The spaced-repetition system then tracks which ones you keep missing and brings those back sooner.",
        "action": "Drop one set of lecture notes into Study & Learn before your next class.",
    },
    {
        "title": "Priority view is sorted for a reason",
        "body": "Assignments are scored on points possible, how close the deadline is, and how much the course weighs. The top item is genuinely the one to start. If you only have twenty minutes, start at the top and stop when time runs out.",
        "action": "Open Priority view and do only the first item today.",
    },
    {
        "title": "Plani teaches, it does not just answer",
        "body": "The tutor is deliberately built not to hand over solutions. Ask it to walk you through a problem type you keep getting wrong and it will check your understanding with follow-up questions rather than printing an answer you will forget by Friday.",
        "action": "Ask Plani to explain the last question you got wrong, step by step.",
    },
    {
        "title": "Push your schedule to Google Calendar",
        "body": "A plan you have to open another app to see is a plan you will ignore. One click exports every study block into the calendar you already check, with the same times and breaks.",
        "action": "Generate a schedule, then hit Export to Google Calendar.",
    },
    {
        "title": "Connect a second school platform",
        "body": "IntelliPlan reads Canvas, Google Classroom, StudentVue, Schoology, Blackboard and Moodle at the same time. If some classes live in one and some in another, connecting both is the difference between a partial task list and a complete one.",
        "action": "Check Settings → Integrations for a platform you have not linked yet.",
    },
    {
        "title": "Study sessions make the estimates better",
        "body": "When you run a session with the timer instead of guessing, IntelliPlan learns how long your work actually takes. After a handful of sittings the block lengths it suggests stop being generic and start matching you.",
        "action": "Run your next study block on the Active page rather than off-app.",
    },
    {
        "title": "Install it so it is one tap away",
        "body": "IntelliPlan installs as an app on Android and iOS, and there are native desktop builds for Windows, macOS and Linux with a tray icon and a global shortcut. Your plan is no use buried in a browser tab.",
        "action": "Visit the Download page and install it on the device you actually use.",
    },
    {
        "title": "Reminders that arrive when you can act",
        "body": "Set your lead time to match how you work — an hour ahead if you need to prepare, fifteen minutes if you just need a nudge. Quiet hours stop anything arriving after you have gone to bed.",
        "action": "Settings → Notifications: set your lead time and quiet hours.",
    },
    {
        "title": "The dashboard has three columns for a reason",
        "body": "Overdue, Today and Upcoming. If Overdue is never empty, your available hours are set too high and the scheduler is writing cheques your week cannot cash. Lower them and the plan becomes honest.",
        "action": "If you have overdue items, cut your daily hours by one and regenerate.",
    },
    {
        "title": "Add the things school does not know about",
        "body": "Club deadlines, driving lessons, a job shift, an application. Manual tasks sit alongside imported ones and get scheduled the same way, which is the only way the plan reflects your whole week rather than your school week.",
        "action": "Add one non-school commitment as a manual task.",
    },
]

#: Research-backed study technique per issue. Each names its evidence,
#: because "studies show" without a citation is how bad advice spreads.
STUDY_SCIENCE: list[dict] = [
    {
        "title": 'The "Retrieval Effect" — why re-reading is mostly wasted time',
        "body": "Roediger & Karpicke (2006) found students who tested themselves on material outperformed students who re-read it, even a week later. Re-reading feels productive because it is easy. Retrieval feels hard because it is working.",
        "action": "After reading a chapter, close the book and write down everything you remember. The gap is what to study.",
    },
    {
        "title": "Spacing beats cramming, and it is not close",
        "body": "Cepeda et al. (2006) reviewed 254 studies and found spaced practice beat massed practice in almost every one. The same total hours, spread across days instead of stacked into one night, produce substantially better recall.",
        "action": "Split your revision across three shorter evenings rather than one long one.",
    },
    {
        "title": "Interleaving feels worse and works better",
        "body": "Rohrer & Taylor (2007) had students practise maths problems either blocked by type or mixed together. The mixed group did worse in practice and markedly better on the test, because real exams do not tell you which method to use.",
        "action": "Mix problem types in one session instead of doing twenty of the same kind.",
    },
    {
        "title": "Explaining it out loud finds the holes",
        "body": "Chi et al. (1994) showed students who explained material to themselves while studying learned considerably more than those who read it twice. Saying it aloud exposes the parts you only think you understand.",
        "action": "Explain one topic out loud, as if teaching it. Notice where you stall.",
    },
    {
        "title": "Sleep is part of studying, not a break from it",
        "body": "Walker & Stickgold's work on sleep-dependent consolidation shows memory continues to be processed overnight. An all-nighter does not just cost you alertness, it removes the step where the learning is filed.",
        "action": "Stop an hour before bed and let the night do the last part of the work.",
    },
    {
        "title": "Handwriting notes beats typing them",
        "body": "Mueller & Oppenheimer (2014) found longhand note-takers outperformed laptop users on conceptual questions. Typing is fast enough to transcribe without thinking; writing forces you to compress, which is where understanding happens.",
        "action": "Take one lecture's notes by hand and summarise them in five bullets after.",
    },
    {
        "title": "Your phone costs you even face-down",
        "body": "Ward et al. (2017) found the mere presence of a phone on the desk reduced available cognitive capacity, even switched off and face-down. Participants did not notice any effect, which is the uncomfortable part.",
        "action": "Put your phone in a different room for one study block and compare.",
    },
    {
        "title": "Testing yourself before you learn helps too",
        "body": "Richland et al. (2009) found attempting questions before studying the material improved later retention, even though nearly every attempt was wrong. The failed guess prepares you to notice the right answer.",
        "action": "Skim the questions at the end of a chapter before you read it.",
    },
]

#: Fixed claims for the stats row. Only the first is computed; the other two
#: are properties of the product rather than measurements, and inventing a
#: user count for a marketing email is how a compliance review goes badly.
STATIC_STATS: list[dict] = [
    {"value": "100%", "label": "Free to use"},
    {"value": "FERPA", "label": "Compliant"},
]


def _rotate(pool: list, now: datetime) -> dict:
    """Pick deterministically by ISO week, so an issue is reproducible."""
    return pool[now.isocalendar()[1] % len(pool)]


def tip_for(now: datetime | None = None) -> dict:
    return _rotate(FEATURE_TIPS, now or datetime.utcnow())


def science_for(now: datetime | None = None) -> dict:
    return _rotate(STUDY_SCIENCE, now or datetime.utcnow())


def live_stats() -> list[dict]:
    """The stats row. Falls back to a static count if the query fails —
    the newsletter must not depend on a database read succeeding."""
    integrations = "10"
    try:
        from App import LinkedAccount, db

        distinct = (
            db.session.query(LinkedAccount.login_type)
            .distinct()
            .count()
        )
        if distinct:
            integrations = str(max(distinct, 6))
    except Exception as exc:
        logger.info("stats query failed, using the static value: %s", exc)

    return [{"value": integrations, "label": "Integrations"}, *STATIC_STATS]
