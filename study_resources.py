"""Pick study resources for one scheduler block.

The design problem
------------------
"Ask the model for helpful links" is the obvious implementation and it is
the wrong one. A language model asked to produce URLs will produce URLs
that look exactly right and 404, because it is recalling the shape of a
link rather than a page it can see. A student who taps two dead links in
their first session stops tapping. Trust is the whole product here, and a
recommendation you cannot open is worse than no recommendation.

So the model does the part it is genuinely good at and none of the part it
is bad at. It chooses **which provider** suits this block and writes the
search terms and a one-line reason. It never writes a URL. We build the URL
from the provider's own search endpoint, which we control and which cannot
be hallucinated.

That keeps the judgement -- "this is a derivatives problem set, send them to
Paul's Online Math Notes rather than a history documentary channel" -- while
making a dead link structurally impossible.

Three sources, best first
-------------------------
1. The student's own course material. If the assignment came from an LMS it
   often carries real links in its description. Those are the best possible
   resource and they are already in our hands, so they need no model at all.
2. A provider the student has linked. Someone who tells us their class
   Quizlet set or Khan course wants to be sent *there*, not to a generic
   search.
3. A provider chosen for the subject, searched for the terms the model
   picked.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

#: A resource is only worth showing if a student can act on it inside one
#: study block. Anything longer is a reading list, not a resource.
MAX_RESULTS = 4


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    #: What it is good for, in the words the model is asked to match on.
    good_for: str
    #: ``{q}`` is replaced with the URL-encoded query. We own this string,
    #: which is the entire point -- the model never supplies a URL.
    search_template: str
    kind: str = "practice"
    #: Free to a student with no account. A paywalled suggestion in a
    #: planner for teenagers is a dead link with extra steps.
    free: bool = True


PROVIDERS = (
    Provider(
        key="khan",
        name="Khan Academy",
        good_for="maths, science and economics, taught from first principles "
                 "with practice questions",
        search_template="https://www.khanacademy.org/search?page_search_query={q}",
        kind="lesson",
    ),
    Provider(
        key="openstax",
        name="OpenStax",
        good_for="free university textbooks: biology, chemistry, physics, "
                 "psychology, economics, statistics",
        search_template="https://openstax.org/search?q={q}",
        kind="textbook",
    ),
    Provider(
        key="mit_ocw",
        name="MIT OpenCourseWare",
        good_for="university-level maths, engineering and computer science, "
                 "including full lecture notes and problem sets",
        search_template="https://ocw.mit.edu/search/?q={q}",
        kind="course",
    ),
    Provider(
        key="paulsnotes",
        name="Paul's Online Math Notes",
        good_for="algebra, calculus and differential equations, with worked "
                 "examples and practice problems",
        search_template="https://tutorial.math.lamar.edu/search.aspx?q={q}",
        kind="practice",
    ),
    Provider(
        key="youtube",
        name="YouTube",
        good_for="a visual explanation of a concept, or a worked example to "
                 "follow along with",
        search_template="https://www.youtube.com/results?search_query={q}",
        kind="video",
    ),
    Provider(
        key="quizlet",
        name="Quizlet",
        good_for="memorising vocabulary, dates, formulas and definitions",
        search_template="https://quizlet.com/search?query={q}&type=sets",
        kind="flashcards",
    ),
    Provider(
        key="wikipedia",
        name="Wikipedia",
        good_for="background and context on a topic, person or event",
        search_template="https://en.wikipedia.org/w/index.php?search={q}",
        kind="reference",
    ),
    Provider(
        key="desmos",
        name="Desmos",
        good_for="graphing a function to see what it actually does",
        search_template="https://www.desmos.com/calculator?q={q}",
        kind="tool",
    ),
    Provider(
        key="purdue_owl",
        name="Purdue OWL",
        good_for="essay structure, citation format (MLA, APA, Chicago) and "
                 "academic writing",
        search_template="https://owl.purdue.edu/site_search.html?q={q}",
        kind="reference",
    ),
    Provider(
        key="gutenberg",
        name="Project Gutenberg",
        good_for="the full text of a classic novel, play or poem that is out "
                 "of copyright",
        search_template="https://www.gutenberg.org/ebooks/search/?query={q}",
        kind="text",
    ),
)

BY_KEY = {p.key: p for p in PROVIDERS}


def build_url(provider_key: str, query: str) -> Optional[str]:
    """A real, openable URL for a provider and a query.

    The only place a resource URL is ever produced. Everything the model
    returns passes through here, so a provider it invented yields nothing
    rather than a link that 404s.
    """
    provider = BY_KEY.get((provider_key or "").strip().lower())
    if not provider:
        return None
    q = (query or "").strip()
    if not q:
        return None
    return provider.search_template.replace("{q}", quote_plus(q))


# ── Links the assignment already carries ─────────────────────────────

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)

#: Links back into the LMS are where the student already is. Suggesting
#: them as a "resource" is noise.
_BORING_HOSTS = (
    "instructure.com", "canvas.net", "blackboard.com", "moodle",
    "schoology.com", "classroom.google.com", "studentvue",
)


def links_in_description(description: str, limit: int = 2) -> list[dict]:
    """Real URLs the teacher put in the assignment itself.

    These beat anything we could pick: a teacher's own link is the actual
    material, and it needs no model and no guessing. They are also the one
    case where a URL we did not construct is safe, because it came from the
    student's own coursework rather than from a model.
    """
    out, seen = [], set()
    for raw in _URL_RE.findall(description or ""):
        url = raw.rstrip(".,;:")
        low = url.lower()
        if any(h in low for h in _BORING_HOSTS):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({
            "title": "From your assignment",
            "provider": "Your course",
            "kind": "course_material",
            "url": url,
            "why": "Your teacher linked this in the assignment.",
            "source": "assignment",
        })
        if len(out) >= limit:
            break
    return out


# ── What a linked account contributes ────────────────────────────────


def linked_account_resources(accounts, course: str = "") -> list[dict]:
    """Resources from providers the student has linked to a course.

    A student who has told us where their class Quizlet set lives wants to
    be sent to *that set*, not to a search for its topic. Their own material
    outranks anything we would pick for them.

    ``accounts`` is an iterable of objects with ``provider``, ``label``,
    ``url`` and optionally ``course`` -- matched case-insensitively against
    the block's course so a linked Biology set does not surface during
    History.
    """
    wanted = (course or "").strip().lower()
    out = []
    for a in accounts or []:
        url = (getattr(a, "url", "") or "").strip()
        if not url:
            continue
        acct_course = (getattr(a, "course", "") or "").strip().lower()
        if acct_course and wanted and acct_course != wanted:
            continue
        provider = BY_KEY.get((getattr(a, "provider", "") or "").lower())
        out.append({
            "title": (getattr(a, "label", "") or "").strip() or "Your saved material",
            "provider": provider.name if provider else (getattr(a, "provider", "") or "Linked"),
            "kind": provider.kind if provider else "course_material",
            "url": url,
            "why": "You linked this to this course.",
            "source": "linked_account",
        })
    return out


# ── Asking the model ─────────────────────────────────────────────────


def _catalogue_for_prompt() -> str:
    return "\n".join(f"- {p.key}: {p.name} — {p.good_for}" for p in PROVIDERS)


def build_prompt(block: dict) -> list[dict]:
    """The model picks a provider and the search terms. Never a URL."""
    title = (block.get("assignment") or block.get("title") or "").strip()
    course = (block.get("course") or "").strip()
    notes = (block.get("notes") or "").strip()
    minutes = block.get("duration_minutes") or 0
    description = (block.get("description") or "")[:1200]

    system = (
        "You choose study resources for one block of a student's study plan. "
        "You pick which provider fits and what to search for. You never write "
        "URLs — the application builds those from the provider and query you "
        "return, so a provider key outside the list below is discarded.\n\n"
        "Available providers:\n" + _catalogue_for_prompt() + "\n\n"
        "Rules:\n"
        f"- Return at most {MAX_RESULTS} resources, fewest that genuinely help.\n"
        "- Match the provider to the work: a calculus problem set is not a "
        "documentary, and an essay is not a flashcard deck.\n"
        "- Vary the kind. Two videos on the same topic is one resource.\n"
        "- Write queries a search box will actually match: the topic, not the "
        "assignment's name. 'Homework 4' finds nothing; 'implicit "
        "differentiation practice' does.\n"
        "- 'why' is one short sentence, addressed to the student, saying what "
        "they will get from it.\n"
        "- If the block is a break or you cannot tell what it is about, "
        "return an empty list rather than guessing."
    )

    user = json.dumps({
        "assignment": title,
        "course": course,
        "notes": notes,
        "minutes_available": minutes,
        "description": description,
    })

    return [
        {"role": "system", "content": system},
        {"role": "user", "content":
            user + "\n\nReturn JSON: {\"resources\": [{\"provider\": \"<key>\", "
                   "\"query\": \"<search terms>\", \"title\": \"<short label>\", "
                   "\"why\": \"<one sentence>\"}]}"},
    ]


def parse_model_resources(raw: str) -> list[dict]:
    """Turn the model's answer into openable resources, dropping the rest.

    Every failure mode is a drop, never a bad link: an invented provider
    key, a missing query, unparseable JSON. The block simply shows fewer
    resources, which is a far smaller cost than one that does not open.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", raw or "", re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return []

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("resources") or []
    else:
        return []

    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (item.get("provider") or "").strip().lower()
        url = build_url(key, item.get("query", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        provider = BY_KEY[key]
        out.append({
            "title": (item.get("title") or "").strip() or provider.name,
            "provider": provider.name,
            "kind": provider.kind,
            "url": url,
            "why": (item.get("why") or "").strip(),
            "source": "suggested",
        })
        if len(out) >= MAX_RESULTS:
            break
    return out


def is_break(block: dict) -> bool:
    return bool(block.get("is_break"))


def resources_for_block(block: dict, *, accounts=(), chat=None) -> list[dict]:
    """Everything worth opening for one block, best source first.

    ``chat`` is injected so this is testable without a model and so a
    deployment with no AI key still returns the student's own links rather
    than nothing.
    """
    if is_break(block):
        return []

    out, have = [], set()

    def add(rows):
        # One dedupe across every source, not per source: a student who
        # linked the same PDF the teacher linked would otherwise see it
        # listed twice, which reads as a bug in the panel.
        for r in rows:
            if r["url"] in have:
                continue
            have.add(r["url"])
            out.append(r)

    add(linked_account_resources(accounts, block.get("course", "")))
    add(links_in_description(block.get("description", "")))

    if chat is not None and len(out) < MAX_RESULTS:
        try:
            raw = chat(build_prompt(block))
        except Exception:
            # The student's own links are still worth showing. An AI outage
            # must not empty the panel.
            raw = ""
        add(parse_model_resources(raw))

    return out[:MAX_RESULTS]
