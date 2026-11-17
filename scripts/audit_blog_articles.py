"""Ad-hoc audit: thin sections and textual overlap in the blog article registry.

Run from the project root:  python scripts/audit_blog_articles.py
"""

from __future__ import annotations

import html
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import blog_articles  # noqa: E402

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "Main_Project" / "templates"

LEGACY_FILES = {
    "best-ai-study-planner": "blog_best_ai_planner.html",
    "best-student-planner-app": "blog_student_planner_app.html",
    "ai-notetaker-for-students": "blog_ai_notetaker.html",
    "how-to-use-canvas-with-a-study-planner": "blog_canvas.html",
    "studentvue-study-planner": "blog_studentvue.html",
    "how-to-prioritize-assignments": "blog_prioritize.html",
    "what-is-a-good-gpa": "blog_good_gpa.html",
    "ap-study-planner": "blog_ap_study_planner.html",
}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def shingles(text: str, n: int = 8) -> set[tuple[str, ...]]:
    w = words(text)
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def legacy_text(name: str) -> str:
    raw = (TEMPLATE_DIR / LEGACY_FILES[name]).read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)\{%.*?%\}", " ", raw)
    raw = re.sub(r"(?s)\{\{.*?\}\}", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def article_text(article: dict) -> str:
    parts = []
    for section in article["sections"]:
        if section.get("h2"):
            parts.append(section["h2"])
        parts.extend(section.get("paragraphs", []))
        parts.extend(section.get("list", []))
    return " ".join(parts)


def main() -> None:
    print(f"articles: {len(blog_articles.ARTICLES)}")
    print("\n== ARTICLE SIZES ==")
    for article in blog_articles.all_articles():
        count = len(words(article_text(article)))
        print(f"{count:5d}w  {len(article['sections']):2d} sections  {article['slug']}")

    print("\n== LEGACY GUIDE OUTLINES ==")
    for name in LEGACY_FILES:
        text = legacy_text(name)
        raw = (TEMPLATE_DIR / LEGACY_FILES[name]).read_text(encoding="utf-8", errors="replace")
        heads = re.findall(r"(?is)<h2[^>]*>(.*?)</h2>", raw)
        heads = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h)).strip() for h in heads]
        print(f"- {name}: {len(words(text))}w; h2s: {' | '.join(heads)}")

    print("\n== THIN SECTIONS (<55 words, no list) ==")
    thin = 0
    for article in blog_articles.all_articles():
        flags = []
        for i, section in enumerate(article["sections"], 1):
            body = " ".join(section.get("paragraphs", []))
            body += " " + " ".join(section.get("list", []))
            count = len(words(body))
            has_list = bool(section.get("list"))
            if count < 55 and not has_list:
                flags.append(f"  s{i} '{section.get('h2', '(intro)')[:44]}' {count}w")
        if flags:
            thin += 1
            print(f"- {article['slug']} ({len(article['sections'])} sections)")
            print("\n".join(flags))
    if not thin:
        print("none")

    # Duplicate subheadings across all articles.
    print("\n== REPEATED H2 HEADINGS ==")
    headings = Counter()
    where: dict[str, list[str]] = {}
    for article in blog_articles.ARTICLES:
        for section in article["sections"]:
            h2 = section.get("h2")
            if h2:
                headings[h2.lower()] += 1
                where.setdefault(h2.lower(), []).append(article["slug"])
    repeated = [(h2, count) for h2, count in headings.most_common() if count > 1]
    for h2, count in repeated:
        print(f"{count}x {h2} -> {', '.join(where[h2])}")
    if not repeated:
        print("none")

    # Pairwise body overlap between new articles.
    print("\n== PAIRWISE OVERLAP (shared 8-grams / min shingles) ==")
    docs = {a["slug"]: shingles(article_text(a)) for a in blog_articles.ARTICLES}
    legacy = {k: shingles(legacy_text(k)) for k in LEGACY_FILES}
    pairs = []
    slugs = list(docs)
    for i, a in enumerate(slugs):
        for b in slugs[i + 1:]:
            inter = len(docs[a] & docs[b])
            union = min(len(docs[a]), len(docs[b])) or 1
            if inter:
                pairs.append((inter / union, inter, a, b))
    for ratio, inter, a, b in sorted(pairs, reverse=True)[:12]:
        print(f"{ratio:5.1%} ({inter}) {a} <> {b}")
    if not pairs:
        print("no shared 8-grams between articles")

    print("\n== OVERLAP NEW ARTICLE vs LEGACY GUIDE ==")
    legacy_pairs = []
    for a in slugs:
        for k, sh in legacy.items():
            inter = len(docs[a] & sh)
            ratio = inter / (min(len(docs[a]), len(sh)) or 1)
            legacy_pairs.append((ratio, inter, a, k))
    shared = [row for row in legacy_pairs if row[1]]
    for ratio, inter, a, k in sorted(shared, reverse=True)[:12]:
        print(f"{ratio:5.1%} ({inter}) {a} <> LEGACY {k}")
    if not shared:
        print("no shared 8-grams with any legacy guide")

    print("\n== SHARED SENTENCES ACROSS ARTICLES (>=9 words) ==")
    sentences: dict[str, list[str]] = {}
    for article in blog_articles.ARTICLES:
        for section in article["sections"]:
            for para in section.get("paragraphs", []):
                for sent in re.split(r"(?<=[.!?])\s+", para):
                    key = " ".join(words(sent)[:12])
                    if len(words(sent)) >= 9:
                        sentences.setdefault(key, []).append(article["slug"])
    for key, slugs_ in sentences.items():
        if len(set(slugs_)) > 1:
            print(key, "->", ", ".join(sorted(set(slugs_))))


if __name__ == "__main__":
    main()
