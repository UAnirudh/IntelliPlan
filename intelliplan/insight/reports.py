"""Turning raw events and answers into the three tables worth looking at.

Pure aggregation over plain tuples, so the admin view has no query logic in
it and every definition here is testable without a database.

Everything is counted in *unique actors*, never in raw events. Raw counts
reward the feature someone refreshes and hide the one everybody opens once,
which is the opposite of what a decision needs.
"""

from __future__ import annotations

from typing import Iterable, Sequence

__all__ = ["feature_usage", "channel_table", "survey_tally", "visit_depth"]


def feature_usage(rows: Iterable[tuple[str, str, str]], limit: int = 25) -> list[dict]:
    """``(kind, rule, actor)`` -> the most-used routes, unique actors first."""
    seen: dict[tuple[str, str], set[str]] = {}
    for kind, rule, actor in rows:
        seen.setdefault((kind, rule), set()).add(actor)
    out = [
        {"kind": kind, "rule": rule, "actors": len(actors)}
        for (kind, rule), actors in seen.items()
    ]
    out.sort(key=lambda row: (-row["actors"], row["rule"]))
    return out[:limit]


def channel_table(
    signups: Iterable[tuple[str, bool, bool]],
    visitors: Iterable[tuple[str, str]] = (),
) -> list[dict]:
    """Per channel: visitors, signups, and how many of those activated.

    ``signups`` is ``(channel, activated, retained)`` per account;
    ``visitors`` is ``(channel, visitor_id)`` for anonymous traffic.

    Signups per visitor is the number that decides where to spend effort: a
    channel with ten thousand visitors and four signups is a channel that
    does not work, however good its traffic looks on its own.
    """
    by_channel: dict[str, dict] = {}

    def row(channel: str) -> dict:
        return by_channel.setdefault(
            channel or "direct",
            {"channel": channel or "direct", "visitors": 0, "signups": 0,
             "activated": 0, "retained": 0, "_seen": set()},
        )

    for channel, visitor in visitors:
        entry = row(channel)
        if visitor not in entry["_seen"]:
            entry["_seen"].add(visitor)
            entry["visitors"] += 1
    for channel, activated, retained in signups:
        entry = row(channel)
        entry["signups"] += 1
        entry["activated"] += 1 if activated else 0
        entry["retained"] += 1 if retained else 0

    out = []
    for entry in by_channel.values():
        entry.pop("_seen", None)
        entry["signup_rate"] = _ratio(entry["signups"], entry["visitors"])
        entry["activation_rate"] = _ratio(entry["activated"], entry["signups"])
        entry["retention_rate"] = _ratio(entry["retained"], entry["signups"])
        out.append(entry)
    out.sort(key=lambda r: (-r["signups"], -r["visitors"], r["channel"]))
    return out


def survey_tally(answers: Iterable[tuple[str, str | None]]) -> dict[str, list[dict]]:
    """``(question, answer)`` -> counts per option, most common first.

    Dismissals are counted as an answer of their own. A question everybody
    skips is telling us something about the question.
    """
    counts: dict[str, dict[str, int]] = {}
    for question, answer in answers:
        bucket = counts.setdefault(question, {})
        key = answer or "(skipped)"
        bucket[key] = bucket.get(key, 0) + 1
    out: dict[str, list[dict]] = {}
    for question, bucket in counts.items():
        total = sum(bucket.values()) or 1
        rows = [
            {"answer": answer, "count": count, "share": round(count / total, 4)}
            for answer, count in bucket.items()
        ]
        rows.sort(key=lambda r: (-r["count"], r["answer"]))
        out[question] = rows
    return out


def visit_depth(rows: Sequence[tuple[str, str, str]]) -> dict:
    """How far anonymous visitors get: one page, a few, or an account.

    The single-page share is the landing page's real bounce rate, and the
    "signed in" count is the only conversion number on the page that is not
    a proxy for one.
    """
    pages: dict[str, set[str]] = {}
    known: set[str] = set()
    for kind, rule, actor in rows:
        if actor.startswith("u:"):
            known.add(actor)
            continue
        pages.setdefault(actor, set()).add(rule)
    one = sum(1 for seen in pages.values() if len(seen) == 1)
    return {
        "visitors": len(pages),
        "one_page_only": one,
        "one_page_share": _ratio(one, len(pages)),
        "multi_page": len(pages) - one,
        "signed_in_actors": len(known),
    }


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0
