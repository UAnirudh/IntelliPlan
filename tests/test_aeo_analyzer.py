"""Tests for the AEO/SEO/GEO website analyzer.

Pure-Python module; we don't make real network calls — we feed sample
HTML to the parser/scorers directly.
"""

from __future__ import annotations

import pytest

import aeo_analyzer
from aeo_analyzer import _Parsed, _score_seo, _score_aeo, _score_geo, analyze


# ── Sample HTML fixtures ─────────────────────────────────────────────


MINIMAL_HTML = """<!doctype html><html><body><p>Hi</p></body></html>"""

GOOD_SEO_HTML = """
<!doctype html><html><head>
<title>Free AI Study Planner for Students | IntelliPlan</title>
<meta name="description" content="IntelliPlan is a free AI study planner that connects to Canvas and StudentVue, builds your study schedule, and tutors you in any subject. Try it free.">
<link rel="canonical" href="https://intelliplan.tech/">
<meta property="og:title" content="IntelliPlan">
<meta property="og:description" content="Free AI study planner for students.">
<meta property="og:image" content="https://intelliplan.tech/static/og.png">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"IntelliPlan","sameAs":["https://wikipedia.org/wiki/IntelliPlan"]}</script>
</head><body>
<h1>The AI study planner built by a student, for students</h1>
<h2>How does it work?</h2>
<p>IntelliPlan is a tool that pulls your Canvas and StudentVue assignments into one feed and builds a study schedule for you.</p>
<h2>What does it cost?</h2>
<p>IntelliPlan is free for students.</p>
<ul><li>One</li><li>Two</li><li>Three</li></ul>
<ul><li>Four</li><li>Five</li></ul>
<a href="/about">About</a><a href="/faq">FAQ</a><a href="/pricing">Pricing</a>
<a href="https://google.com">Google</a>
<p>Last updated by Anirudh on June 13, 2026. According to a recent study, 78% of students miss assignments. IntelliPlan was founded in 2025.</p>
""" + ("This is a longer paragraph with enough words to push past the 600-word minimum threshold. " * 50) + """
</body></html>
"""

FAQ_PAGE_HTML = """
<!doctype html><html><head>
<title>FAQ</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[]}</script>
</head><body>
<h1>Frequently Asked Questions</h1>
<h2>How do I sign up?</h2>
<p>Click the button.</p>
<h2>Is it free?</h2>
<p>Yes. IntelliPlan is free.</p>
<h2>What is AEO?</h2>
<p>AEO is Answer Engine Optimization.</p>
<ul><li>One</li><li>Two</li></ul>
<p>Written by Anirudh, last updated June 2026. According to research, FAQ pages perform 40% better.</p>
</body></html>
"""


# ── URL normalization ────────────────────────────────────────────────


def test_normalize_adds_https():
    assert aeo_analyzer._normalize_url("example.com") == "https://example.com"


def test_normalize_preserves_http():
    assert aeo_analyzer._normalize_url("http://example.com") == "http://example.com"


def test_normalize_strips_whitespace():
    assert aeo_analyzer._normalize_url("  example.com  ") == "https://example.com"


def test_normalize_empty():
    assert aeo_analyzer._normalize_url("") == ""


# ── Parser ───────────────────────────────────────────────────────────


def test_parser_extracts_title():
    p = _Parsed(GOOD_SEO_HTML)
    assert "IntelliPlan" in p.title()


def test_parser_extracts_meta_description():
    p = _Parsed(GOOD_SEO_HTML)
    assert "free ai study planner" in p.meta("description").lower()


def test_parser_finds_h1():
    p = _Parsed(GOOD_SEO_HTML)
    assert len(p.headings()["h1"]) == 1


def test_parser_extracts_jsonld():
    p = _Parsed(GOOD_SEO_HTML)
    chunks = p.jsonld()
    assert len(chunks) >= 1
    assert chunks[0]["@type"] == "Organization"


def test_parser_visible_text_strips_scripts():
    html = "<html><head><script>var x=1;</script></head><body>Hello world</body></html>"
    p = _Parsed(html)
    text = p.visible_text()
    assert "Hello world" in text
    assert "var x" not in text


def test_parser_handles_empty_html():
    p = _Parsed("")
    assert p.title() == ""
    assert p.meta("description") == ""
    assert p.jsonld() == []


# ── Scorer: SEO ──────────────────────────────────────────────────────


def test_seo_scorer_low_for_minimal():
    p = _Parsed(MINIMAL_HTML)
    cat = _score_seo(p, "https://example.com")
    assert cat.score < 40
    # Must surface specific findings
    severities = [f["severity"] for f in cat.findings]
    assert "critical" in severities or "high" in severities


def test_seo_scorer_high_for_good_page():
    p = _Parsed(GOOD_SEO_HTML)
    cat = _score_seo(p, "https://intelliplan.tech")
    assert cat.score >= 60


def test_seo_scorer_flags_no_title():
    p = _Parsed("<html><body><p>Hi</p></body></html>")
    cat = _score_seo(p, "https://example.com")
    msgs = " ".join(f["message"] for f in cat.findings).lower()
    assert "title" in msgs


def test_seo_scorer_flags_noindex():
    html = '<html><head><title>X</title><meta name="robots" content="noindex"></head><body>x</body></html>'
    p = _Parsed(html)
    cat = _score_seo(p, "https://example.com")
    severities = [f["severity"] for f in cat.findings]
    assert "critical" in severities


# ── Scorer: AEO ──────────────────────────────────────────────────────


def test_aeo_scorer_rewards_faq_schema():
    """An identical page WITH FAQPage schema must score higher than WITHOUT it."""
    base_body = """<title>Test</title></head><body>
    <h1>Hello</h1>
    <h2>How do I sign up?</h2><p>Click the button.</p>
    <h2>Is it free?</h2><p>Yes. It is a free tool.</p>
    <h2>What does it do?</h2><p>It does many things.</p>
    <ul><li>One</li><li>Two</li></ul><ul><li>A</li><li>B</li></ul>
    <p>Written by Author. Last updated June 2026. According to research, 78% engagement.</p>
    </body></html>"""
    without_faq = "<!doctype html><html><head>" + base_body
    with_faq = (
        "<!doctype html><html><head>"
        '<script type="application/ld+json">{"@type":"FAQPage","mainEntity":[]}</script>'
        + base_body
    )
    s_with = _score_aeo(_Parsed(with_faq)).score
    s_without = _score_aeo(_Parsed(without_faq)).score
    assert s_with > s_without, f"FAQ schema should boost AEO score (with={s_with}, without={s_without})"


def test_aeo_scorer_flags_no_questions():
    html = "<html><head><title>X</title></head><body><p>Just a statement.</p></body></html>"
    p = _Parsed(html)
    cat = _score_aeo(p)
    msgs = " ".join(f["message"] for f in cat.findings).lower()
    assert "question" in msgs or "q&a" in msgs or "qa" in msgs


# ── Scorer: GEO ──────────────────────────────────────────────────────


def test_geo_scorer_rewards_entity_schema():
    p = _Parsed(GOOD_SEO_HTML)
    cat = _score_geo(p)
    # Organization schema present + sameAs present → should score
    assert cat.score >= 20


def test_geo_scorer_flags_missing_entities():
    html = "<html><head><title>X</title></head><body><p>the quick brown fox jumps over.</p></body></html>"
    p = _Parsed(html)
    cat = _score_geo(p)
    msgs = " ".join(f["message"] for f in cat.findings).lower()
    assert "entity" in msgs or "entities" in msgs


# ── End-to-end ───────────────────────────────────────────────────────


def test_analyze_rejects_empty_url():
    with pytest.raises(ValueError):
        analyze("")


def test_analyze_score_categories(monkeypatch):
    """Inject GOOD_SEO_HTML directly so the test doesn't hit the network."""
    def fake_fetch(url, prefer_apify=True):
        return GOOD_SEO_HTML, "https://intelliplan.tech/", 42, "test"
    monkeypatch.setattr(aeo_analyzer, "fetch_page", fake_fetch)
    result = analyze("https://intelliplan.tech")
    assert result.overall == round((result.seo.score + result.aeo.score + result.geo.score) / 3)
    assert 0 <= result.seo.score <= 100
    assert 0 <= result.aeo.score <= 100
    assert 0 <= result.geo.score <= 100
    assert result.fetched_via == "test"
    assert result.page_meta["title"]


def test_analyze_returns_recommendations(monkeypatch):
    def fake_fetch(url, prefer_apify=True):
        return MINIMAL_HTML, "https://example.com", 10, "test"
    monkeypatch.setattr(aeo_analyzer, "fetch_page", fake_fetch)
    result = analyze("https://example.com")
    # A nearly-empty page must trigger recommendations
    assert len(result.recommendations) > 0


def test_analyze_serializable(monkeypatch):
    """to_dict() must produce a JSON-serializable structure."""
    import json
    def fake_fetch(url, prefer_apify=True):
        return GOOD_SEO_HTML, "https://intelliplan.tech/", 10, "test"
    monkeypatch.setattr(aeo_analyzer, "fetch_page", fake_fetch)
    result = analyze("https://intelliplan.tech")
    d = result.to_dict()
    json.dumps(d)  # must not raise
    assert "seo" in d and "aeo" in d and "geo" in d
    assert "recommendations" in d
