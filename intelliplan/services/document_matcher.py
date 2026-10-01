"""Match an assignment to the student's own documents in Drive and OneDrive.

The tutor and the study map were grounded only in what Canvas attached to an
assignment. For most students that is a one-line prompt; the material they
actually study from -- their class notes, the teacher's slides they saved,
last unit's outline -- lives in Google Drive or OneDrive, and the AI never
saw it. This module finds those files for an assignment and turns them into
the same ``{"name", "text"}`` material entries the Canvas path produces, so
every downstream consumer (``assignment_prompt``, ``assignment_study_map``)
uses them without knowing where they came from.

How a match is decided
----------------------
1. **Keywords** from the title (weighted highest), the course, and the
   description, minus stopwords and classroom boilerplate ("assignment",
   "period 3"), which appear in every file and so distinguish none.
2. **Provider search** casts the net: Drive's full-text search and Graph's
   drive search both look inside documents, not just at names.
3. **Rank** by term overlap -- a hit in the file *name* counts three times a
   hit in the body, because a student names a file for what it is -- plus a
   bonus for the course name and a recency bonus that halves every 60 days.
   Last month's notes beat last year's for the same words.
4. **Read** only the top few, capped in size, and drop anything whose text
   turned out to share nothing with the assignment.

Transport lives in ``google_drive_helper`` / ``onedrive_helper``; tokens and
persistence live in App. This module is pure logic plus two thin adapters, so
it can be tested without either provider.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been
before being below between both but by can could did do does doing down during
each few for from further had has have having he her here hers herself him
himself his how i if in into is it its itself just me more most my myself no nor
not now of off on once only or other our ours ourselves out over own same she
should so some such than that the their theirs them themselves then there these
they this those through to too under until up very was we were what when where
which while who whom why will with would you your yours yourself yourselves
also use using used make made get one two three first second new via per etc
""".split())

#: Words that appear on nearly every piece of schoolwork. As search terms
#: they match everything, which is the same as matching nothing.
CLASSROOM_WORDS = frozenset("""
assignment assignments homework hw due date dates worksheet worksheets task tasks
submit submission complete completed period section sec class classes course
courses grade grades graded points point pts honors student students teacher
please read answer answers question questions page pages week weeks day days
today tomorrow part parts final draft rough copy file files doc docs document
documents google drive onedrive intelliplan untitled notes note
""".split())

TITLE_WEIGHT = 2.0
COURSE_WEIGHT = 1.0
DESCRIPTION_WEIGHT = 1.0
NAME_HIT = 3.0
TEXT_HIT = 1.0
COURSE_BONUS = 1.5
RECENCY_MAX = 1.5
RECENCY_HALF_LIFE_DAYS = 60

MAX_KEYWORDS = 6
MAX_DOCS = 4
MAX_DOC_CHARS = 4000
MAX_TOTAL_CHARS = 9000

PROVIDER_LABELS = {"google_drive": "Google Drive", "onedrive": "OneDrive"}

_TOKEN = re.compile(r"[a-z0-9][a-z0-9'\-]*[a-z0-9]|[a-z0-9]")


def _norm(term: str) -> str:
    """Crude singular form, so "cells" in a title finds "cell" in the notes.

    Deliberately not a real stemmer: the cost of a wrong merge is a slightly
    worse rank, and this needs no dependency and no language model.
    """
    term = term.lower().strip("'-")
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def tokens(text: str) -> list:
    return [t for t in _TOKEN.findall(str(text or "").lower())]


def _useful(term: str) -> bool:
    return (len(term) >= 3 and not term.isdigit()
            and term not in STOPWORDS and term not in CLASSROOM_WORDS)


def extract_keywords(title: str, course: str = "", description: str = "",
                     limit: int = MAX_KEYWORDS) -> list:
    """Ordered search terms for an assignment: title first, then the words
    that recur in the description, then the course.

    Order matters because providers are sent only the first few, and the
    title is the student's (or teacher's) own summary of what this is.
    """
    weights: dict = {}
    display: dict = {}

    def add(term, weight):
        key = _norm(term)
        if not _useful(key):
            return
        weights[key] = weights.get(key, 0.0) + weight
        display.setdefault(key, term.strip("'-"))

    for position, term in enumerate(tokens(title)):
        # Earlier title words edge out later ones on ties ("Photosynthesis
        # lab report" -- the subject comes first far more often than not).
        add(term, TITLE_WEIGHT + 0.01 * max(0, 10 - position))
    counts = Counter(_norm(t) for t in tokens(description)[:400])
    for term in tokens(description)[:400]:
        if counts[_norm(term)] >= 2:
            add(term, DESCRIPTION_WEIGHT * 0.5)
    for term in tokens(course):
        add(term, COURSE_WEIGHT)
    ranked = sorted(weights, key=lambda k: (-weights[k], k))
    return [display[k] for k in ranked[:max(1, limit)]]


def course_terms(course: str) -> list:
    return [_norm(t) for t in tokens(course) if _useful(_norm(t))]


# ── Ranking ───────────────────────────────────────────────────────────


def _parse_time(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())
        text = f"{head}.{digits[:6]}{tail[len(digits):]}" if digits else head + tail[len(digits):]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def recency_bonus(modified, now: Optional[datetime] = None) -> float:
    when = _parse_time(modified)
    if not when:
        return 0.0
    now = now or datetime.now(timezone.utc)
    age_days = max(0.0, (now - when).total_seconds() / 86400.0)
    return RECENCY_MAX * math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)


def score(candidate: dict, keywords: Iterable, course: str = "", text: str = "",
          now: Optional[datetime] = None) -> dict:
    """``{"score", "name_hits", "text_hits"}`` for one file."""
    wanted = {_norm(k) for k in keywords if k}
    name_terms = {_norm(t) for t in tokens(candidate.get("name", ""))}
    name_hits = len(wanted & name_terms)
    text_terms = {_norm(t) for t in tokens(text[:20000])} if text else set()
    text_hits = len(wanted & text_terms)
    course_set = set(course_terms(course))
    course_hit = bool(course_set & (name_terms | text_terms))
    value = (NAME_HIT * name_hits + TEXT_HIT * text_hits
             + (COURSE_BONUS if course_hit else 0.0)
             + recency_bonus(candidate.get("modified"), now))
    return {"score": round(value, 4), "name_hits": name_hits, "text_hits": text_hits}


def rank(candidates: list, keywords: list, course: str = "", now=None) -> list:
    """Candidates ordered best-first on name overlap and recency alone -- the
    cheap pass that decides which few are worth downloading."""
    seen = set()
    out = []
    for item in candidates:
        key = (item.get("provider"), item.get("id"))
        if key in seen:
            continue
        seen.add(key)
        out.append({**item, **score(item, keywords, course, now=now)})
    out.sort(key=lambda c: (-c["score"], c.get("name", "").lower()))
    return out


def excerpt(text: str, keywords: Iterable, width: int = 240) -> str:
    """A short window of ``text`` around the first keyword it contains."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    lowered = text.lower()
    hits = [lowered.find(str(k).lower()) for k in keywords if k]
    hits = [h for h in hits if h >= 0]
    start = max(0, min(hits) - width // 3) if hits else 0
    snippet = text[start:start + width].strip()
    return ("…" if start else "") + snippet + ("…" if start + width < len(text) else "")


# ── Sources ───────────────────────────────────────────────────────────


@dataclass
class Source:
    """One connected provider, as the matcher sees it."""

    provider: str
    search: Callable[[list, str], list]
    fetch_text: Callable[[dict], str]

    @property
    def label(self) -> str:
        return PROVIDER_LABELS.get(self.provider, self.provider)


def google_drive_source(client) -> Source:
    import google_drive_helper as gd

    def search(keywords, course):
        # One OR query covers every term: Drive does the fan-out server side.
        return gd.search_files(client, keywords[:MAX_KEYWORDS], limit=20)

    return Source("google_drive", search, lambda item: gd.fetch_text(client, item, MAX_DOC_CHARS * 2))


def onedrive_source(client) -> Source:
    import onedrive_helper as od

    def search(keywords, course):
        # Graph's drive search takes one phrase. Two narrow queries find more
        # than one long one, which tends to match nothing: the two strongest
        # title words together, then the strongest alone.
        queries = []
        if len(keywords) >= 2:
            queries.append(" ".join(keywords[:2]))
        if keywords:
            queries.append(keywords[0])
        found = []
        for query in dict.fromkeys(queries):
            try:
                found.extend(od.search_files(client, query, limit=15))
            except ValueError:
                continue
        return found

    return Source("onedrive", search, lambda item: od.fetch_text(client, item, MAX_DOC_CHARS * 2))


# ── Matching ──────────────────────────────────────────────────────────

_cache: dict = {}
_cache_lock = threading.Lock()
CACHE_TTL = 600
CACHE_MAX = 256


def _cache_key(owner, providers, title, course, description):
    return (owner, tuple(sorted(providers)),
            re.sub(r"\s+", " ", f"{title}|{course}|{description[:500]}".lower()).strip())


def clear_cache():
    with _cache_lock:
        _cache.clear()


def find_documents(sources: list, title: str, course: str = "", description: str = "",
                   limit: int = MAX_DOCS, with_text: bool = True, owner=None,
                   now: Optional[datetime] = None) -> dict:
    """Search every connected source and return the best-matching documents.

    Returns ``{"keywords", "documents", "errors"}``. A provider that fails
    contributes an entry to ``errors`` (a provider name and a reason code,
    never a message that might carry a token) and nothing else: one account
    needing a reconnect must not hide the other account's matches.

    ``owner`` keys a short in-process cache. The tutor re-sends the selected
    assignment with every message of a conversation, and re-searching two
    clouds per chat turn would add seconds to every reply for the same result.
    """
    from cloud_token_client import TokenExpired

    keywords = extract_keywords(title, course, description)
    result = {"keywords": keywords, "documents": [], "errors": []}
    if not keywords or not sources:
        return result

    key = None
    if owner is not None:
        key = _cache_key(owner, [s.provider for s in sources], title, course, description)
        with _cache_lock:
            hit = _cache.get(key)
            if hit and hit[0] > time.time() and (hit[1].get("with_text") or not with_text):
                return {k: v for k, v in hit[1].items() if k != "with_text"}

    candidates = []
    by_provider = {}
    for source in sources:
        try:
            found = source.search(keywords, course) or []
        except TokenExpired:
            result["errors"].append({"provider": source.provider, "reason": "reconnect"})
            continue
        except Exception as exc:
            print(f"[document-matcher] {source.provider} search failed: {type(exc).__name__}")
            result["errors"].append({"provider": source.provider, "reason": "unavailable"})
            continue
        by_provider[source.provider] = source
        candidates.extend(dict(item, provider=source.provider) for item in found)

    ranked = rank(candidates, keywords, course, now=now)
    chosen = []
    budget = MAX_TOTAL_CHARS
    # Read a few more than we return: some files turn out empty or unrelated,
    # and it is cheaper to over-read by two than to come back with nothing.
    for item in ranked[: max(limit + 2, limit)]:
        if len(chosen) >= limit:
            break
        text = ""
        if with_text:
            try:
                text = by_provider[item["provider"]].fetch_text(item) or ""
            except TokenExpired:
                continue
            except Exception as exc:
                print(f"[document-matcher] {item['provider']} read failed: {type(exc).__name__}")
                text = ""
            rescored = score(item, keywords, course, text=text, now=now)
            item = {**item, **rescored}
            # Provider search already said the content matched; if we could
            # read it and it shares nothing with the assignment, the provider
            # matched a word we did not ask about. If we could not read it,
            # a name hit is still worth listing -- but not worth feeding the AI.
            if rescored["name_hits"] == 0 and rescored["text_hits"] == 0:
                continue
        text = text[:min(MAX_DOC_CHARS, max(0, budget))]
        budget -= len(text)
        chosen.append({
            "provider": item["provider"],
            "provider_label": PROVIDER_LABELS.get(item["provider"], item["provider"]),
            "id": item.get("id"),
            "name": item.get("name"),
            "url": item.get("url") or "",
            "modified": item.get("modified") or "",
            "mime": item.get("mime") or "",
            "score": item["score"],
            "excerpt": excerpt(text, keywords) if text else "",
            "text": text,
        })
    chosen.sort(key=lambda d: -d["score"])
    result["documents"] = chosen

    if key is not None and not result["errors"]:
        with _cache_lock:
            if len(_cache) >= CACHE_MAX:
                oldest = min(_cache, key=lambda k: _cache[k][0])
                _cache.pop(oldest, None)
            _cache[key] = (time.time() + CACHE_TTL, {**result, "with_text": with_text})
    return result


def public_documents(documents: list) -> list:
    """Documents without their full text -- for JSON responses to the browser,
    which only needs enough to show and link them."""
    return [{k: v for k, v in d.items() if k != "text"} for d in documents]


def augment_context(context: dict, documents: list, max_total_chars: int = MAX_TOTAL_CHARS) -> dict:
    """Append matched documents to an assignment context as materials.

    Canvas attachments stay first: they are what the teacher attached to
    *this* assignment, and the student's own files are supporting material.
    The name says where each came from, so the tutor can cite "your Google
    Drive notes" rather than implying the teacher supplied them.
    """
    materials = list(context.get("materials") or [])
    have = {m.get("name") for m in materials}
    budget = max_total_chars
    for doc in documents:
        text = str(doc.get("text") or "").strip()
        if not text or budget <= 0:
            continue
        name = f"Your {doc.get('provider_label') or 'document'}: {doc.get('name')}"[:160]
        if name in have:
            continue
        piece = text[:budget]
        materials.append({"name": name, "text": piece, "source": doc.get("provider")})
        have.add(name)
        budget -= len(piece)
    return {**context, "materials": materials}


def context_from_documents(title: str, course: str, description: str, documents: list) -> dict:
    """An assignment context built only from the student's own files.

    For work that did not come from Canvas -- a StudentVue assignment, a task
    typed into the planner -- this is the only source text there is.
    """
    base = {"title": str(title or "Assignment")[:160],
            "description": re.sub(r"\s+", " ", str(description or "")).strip()[:5000],
            "materials": [], "skipped_count": 0, "total_files": 0}
    if course:
        base["description"] = (f"Course: {course}. " + base["description"]).strip()
    return augment_context(base, documents)


# ── Study guide ───────────────────────────────────────────────────────


def key_terms(documents: list, keywords: list, limit: int = 8) -> list:
    """The words the student's own documents use most, for a glossary to
    fill in. Frequency across *their* files, so it reflects how their class
    talks about the topic rather than a generic list."""
    counts = Counter()
    for doc in documents:
        for term in tokens(doc.get("text", "")[:20000]):
            norm = _norm(term)
            if _useful(norm) and len(norm) >= 4:
                counts[norm] += 1
    wanted = [_norm(k) for k in keywords]
    ordered = [k for k in wanted if counts.get(k)]
    ordered += [t for t, n in counts.most_common(limit * 3) if n >= 2 and t not in ordered]
    return ordered[:limit]


def _due(value) -> Optional[date]:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


DEFAULT_STEPS = (
    "Re-read the directions and list every deliverable",
    "Skim your related documents below and pull out what applies",
    "Outline your answer before writing",
    "Draft it",
    "Check the draft against the directions, then submit",
)


def build_study_guide(title: str, course: str = "", description: str = "", due_date: str = "",
                      documents: Optional[list] = None, steps: Optional[list] = None,
                      today: Optional[date] = None) -> dict:
    """A Markdown study guide for one assignment: ``{"title", "markdown"}``.

    Deterministic by default, so "Create study doc" works with no AI quota
    left and produces the same thing twice. ``steps`` lets a caller that did
    get a model-written plan (the study map) substitute its steps.

    With a due date, the plan's steps are spread across the days remaining,
    because "outline, draft, check" is only a plan once it says when.
    """
    documents = documents or []
    title = re.sub(r"\s+", " ", str(title or "Assignment")).strip()[:160] or "Assignment"
    keywords = extract_keywords(title, course, description)
    today = today or date.today()
    due = _due(due_date)
    plan = [re.sub(r"\s+", " ", str(s)).strip()[:200] for s in (steps or DEFAULT_STEPS) if str(s).strip()][:8]

    lines = [f"# Study guide: {title}", ""]
    meta = []
    if course:
        meta.append(f"**Course:** {course}")
    if due:
        meta.append(f"**Due:** {due.strftime('%A, %B %d, %Y')}")
    if meta:
        lines += [" · ".join(meta), ""]

    lines += ["## What the assignment asks", ""]
    directions = re.sub(r"\s+", " ", str(description or "")).strip()
    lines += [directions[:3000] if directions else "Paste the directions here so you can check your work against them.", ""]

    lines += ["## Plan", ""]
    days_left = (due - today).days if due else 0
    for index, step in enumerate(plan):
        if due and days_left >= 1:
            # Evenly spaced, last step the day before it is due.
            offset = round(index * max(0, days_left - 1) / max(1, len(plan) - 1))
            when = today + timedelta(days=offset)
            lines.append(f"- [ ] {when.strftime('%a %b %d')}: {step}")
        else:
            lines.append(f"- [ ] {step}")
    lines.append("")

    terms = key_terms(documents, keywords)
    if terms:
        lines += ["## Key terms to define in your own words", ""]
        lines += [f"- **{t}**: " for t in terms]
        lines.append("")

    lines += ["## Your related documents", ""]
    if documents:
        for doc in documents:
            label = doc.get("provider_label") or PROVIDER_LABELS.get(doc.get("provider"), "")
            when = (_parse_time(doc.get("modified")) or None)
            edited = f", edited {when.date().isoformat()}" if when else ""
            name = str(doc.get("name") or "Document")
            url = str(doc.get("url") or "")
            head = f"[{name}]({url})" if url.startswith("https://") else name
            lines.append(f"- {head} ({label}{edited})")
            snippet = doc.get("excerpt") or excerpt(doc.get("text", ""), keywords)
            if snippet:
                lines.append(f"  {snippet}")
    else:
        lines.append("No matching files were found. Add your notes here as you go.")
    lines += ["", "## Notes", "", "Created by IntelliPlan."]
    return {"title": f"{title} study guide"[:180], "markdown": "\n".join(lines)}
