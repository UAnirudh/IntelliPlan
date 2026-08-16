"""What changed on the site, in language a student would read.

The weekly newsletter sends without a human looking at it, so this module
carries the whole burden of not embarrassing anyone. Two rules follow from
that:

* **Allow-list, never deny-list.** Only commits whose type is in
  ``USER_FACING_TYPES`` are considered. A refactor, a dependency bump, a
  secret rotation or a commit with an unrecognised prefix is dropped
  silently. A deny-list would leak the first thing nobody thought to ban.
* **Scopes are translated, not printed.** ``feat(desktop)`` becomes
  "Desktop app", not "desktop". Anything whose scope is not in
  ``SCOPE_LABELS`` is dropped rather than shown raw — internal scope names
  are internal.

``.git`` is excluded from the Docker build context, so ``git log`` returns
nothing in production. The runtime path is therefore: try git (works
locally and in CI), and fall back to the snapshot committed at
``intelliplan/email/changelog_snapshot.json``. Refresh the snapshot with
``python scripts/refresh_changelog.py``.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
import subprocess
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = pathlib.Path(__file__).parent / "changelog_snapshot.json"

#: Conventional-commit types a student could care about. Everything else —
#: refactor, chore, test, ci, docs, build, style, perf — is invisible to
#: them and stays out.
USER_FACING_TYPES = {"feat": "New", "fix": "Fixed"}

#: Internal scope -> the words a student would recognise. A commit whose
#: scope is missing from this map is dropped: showing a raw scope like
#: "glue" or "sw" tells them nothing and looks unfinished.
SCOPE_LABELS = {
    "web": "Website",
    "ui": "Interface",
    "desktop": "Desktop app",
    "mobile": "Mobile app",
    "scheduler": "Scheduler",
    "planner": "Scheduler",
    "study": "Study & Learn",
    "tutor": "Plani tutor",
    "chatbot": "Plani",
    "canvas": "Canvas",
    "studentvue": "StudentVue",
    "schoology": "Schoology",
    "classroom": "Google Classroom",
    "calendar": "Google Calendar",
    "notion": "Notion",
    "grades": "Grades",
    "dashboard": "Dashboard",
    "active": "Study sessions",
    "streak": "Streaks",
    "notifications": "Reminders",
    # Deliberately no "email" scope. That scope covers this very subsystem —
    # the sending plumbing, the consent gate, the dedup ledger. It is real
    # work and it is of no interest whatsoever to a student, and left in it
    # produced newsletter items reading "Lifecycle email — consent gate,
    # HTML templates, dedup ledger". Anything student-visible about email
    # lands under "notifications" instead.
    "download": "Downloads",
    "extension": "Chrome extension",
    "offline": "Offline mode",
    "a11y": "Accessibility",
    "sync": "Sync",
}

#: `type(scope): subject`
_COMMIT_RE = re.compile(r"^(?P<type>[a-z]+)\((?P<scope>[a-z0-9_-]+)\):\s*(?P<subject>.+)$")

#: Anything matching these never reaches a student, whatever its type says.
#: Belt-and-braces on top of the allow-list, because the cost of a leak here
#: is an email nobody can recall.
_NEVER = re.compile(
    r"\b(secret|token|api[_ -]?key|password|credential|vuln|exploit|cve|"
    r"hotfix|rollback|revert|wip|temp|hack|internal|admin)\b",
    re.IGNORECASE,
)


def _git_log_since(since: datetime) -> list[str]:
    """Subjects of commits on the current branch since ``since``.

    Returns [] on any failure — no git binary, no .git directory, a
    detached worktree. Production hits that path every time and must carry
    on rather than raise.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--since={since:%Y-%m-%d}",
                "--no-merges",
                "--pretty=format:%s",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=pathlib.Path(__file__).resolve().parents[2],
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception as exc:
        logger.info("git log unavailable (expected in production): %s", exc)
        return []


def _load_snapshot() -> list[dict]:
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        return data.get("commits", []) if isinstance(data, dict) else []
    except Exception:
        return []


def humanise(subject: str) -> dict | None:
    """Turn one commit subject into a newsletter item, or None to drop it.

    Dropping is the default. Every branch that is not a confident match
    returns None.
    """
    subject = (subject or "").strip()
    if not subject or _NEVER.search(subject):
        return None

    match = _COMMIT_RE.match(subject)
    if not match:
        return None

    commit_type = match.group("type")
    if commit_type not in USER_FACING_TYPES:
        return None

    label = SCOPE_LABELS.get(match.group("scope"))
    if not label:
        return None

    body = match.group("subject").strip()
    if len(body) < 12 or len(body) > 140:
        # Too short to mean anything, or a paragraph someone crammed into a
        # subject line. Neither reads well in a feature card.
        return None

    return {
        "tag": f"{USER_FACING_TYPES[commit_type]} · {label}",
        "title": body[0].upper() + body[1:],
        "body": "",
    }


def recent_changes(since: datetime | None = None, limit: int = 5) -> list[dict]:
    """User-facing changes since ``since``, newest first, ready for the
    newsletter's ``features`` list."""
    since = since or (datetime.utcnow() - timedelta(days=7))

    subjects = _git_log_since(since)
    if not subjects:
        cutoff = since.isoformat()
        subjects = [
            row.get("subject", "")
            for row in _load_snapshot()
            if row.get("date", "") >= cutoff
        ]

    items, seen = [], set()
    for subject in subjects:
        item = humanise(subject)
        if item is None or item["title"].lower() in seen:
            continue
        seen.add(item["title"].lower())
        items.append(item)
        if len(items) >= limit:
            break
    return items
