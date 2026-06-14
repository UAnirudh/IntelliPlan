"""AEO/SEO/GEO website analyzer — polsia-style.

Public, lead-generating tool. The user pastes a URL, we fetch and parse
the page (via Apify's `rag-web-browser` actor when an APIFY_TOKEN is
configured; otherwise we fall back to direct HTTP fetch with requests),
and produce a score for:

  - **SEO** — title, meta description, headings, canonical, robots,
    Open Graph, Twitter card, structured data, internal linking.
  - **AEO** (Answer Engine Optimization) — FAQ / HowTo schema, clear
    Q&A patterns, definition sentences, listicle structure, citations.
  - **GEO** (Generative Engine Optimization) — entity coverage, factual
    density, semantic clarity, source attribution, citation worthiness.

The output is a JSON blob with category scores, found-issues, and
actionable recommendations. Pure-Python; no LLM call required, so the
endpoint is free to run and can be rate-limited generously.

Flask blueprint registered in App.py.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any
from urllib.parse import urlparse, urljoin

import requests
from flask import Blueprint, jsonify, render_template, request

# ── HTML parsing — use bs4 if available, fall back to regex shim ─────
try:
    from bs4 import BeautifulSoup  # type: ignore
    _HAS_BS4 = True
except Exception:
    BeautifulSoup = None  # type: ignore
    _HAS_BS4 = False


aeo_bp = Blueprint("aeo_analyzer", __name__)

APIFY_TOKEN = os.environ.get("APIFY_TOKEN") or os.environ.get("APIFY_API_TOKEN", "")
USER_AGENT = "IntelliPlanAEO/1.0 (+https://intelliplan.tech/aeo)"
FETCH_TIMEOUT_S = 18


# ── Result types ────────────────────────────────────────────────────


@dataclass
class CategoryScore:
    name: str
    score: int            # 0-100
    max_score: int = 100
    findings: list[dict] = field(default_factory=list)

    def add(self, severity: str, message: str, hint: str | None = None) -> None:
        self.findings.append({"severity": severity, "message": message, "hint": hint or ""})


@dataclass
class AnalysisResult:
    url: str
    final_url: str
    fetched_at: float
    fetch_ms: int
    fetched_via: str       # "apify" | "direct"
    seo: CategoryScore = field(default_factory=lambda: CategoryScore(name="SEO", score=0))
    aeo: CategoryScore = field(default_factory=lambda: CategoryScore(name="AEO", score=0))
    geo: CategoryScore = field(default_factory=lambda: CategoryScore(name="GEO", score=0))
    overall: int = 0
    page_meta: dict = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "fetched_at": self.fetched_at,
            "fetch_ms": self.fetch_ms,
            "fetched_via": self.fetched_via,
            "seo": asdict(self.seo),
            "aeo": asdict(self.aeo),
            "geo": asdict(self.geo),
            "overall": self.overall,
            "page_meta": self.page_meta,
            "recommendations": self.recommendations,
        }


# ── Fetchers ────────────────────────────────────────────────────────


def _normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    return raw


def _fetch_direct(url: str) -> tuple[str, str, int]:
    """Plain HTTP fetch. Returns (html, final_url, elapsed_ms)."""
    t0 = time.perf_counter()
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    r = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT_S, allow_redirects=True)
    elapsed = int((time.perf_counter() - t0) * 1000)
    r.raise_for_status()
    return (r.text, r.url, elapsed)


def _fetch_via_apify(url: str) -> tuple[str, str, int]:
    """Use Apify's rag-web-browser actor to render and return HTML.

    Falls back to direct fetch on any error so the analyzer is never
    blocked by Apify being misconfigured.
    """
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN not configured")
    t0 = time.perf_counter()
    # Apify's rag-web-browser actor: run-sync-get-dataset-items endpoint
    actor_id = "apify~rag-web-browser"
    endpoint = (
        f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
        f"?token={APIFY_TOKEN}&timeout=20"
    )
    body = {
        "query": url,
        "maxResults": 1,
        "outputFormats": ["html", "markdown"],
        "requestTimeoutSecs": 15,
    }
    r = requests.post(endpoint, json=body, timeout=FETCH_TIMEOUT_S + 4)
    r.raise_for_status()
    data = r.json()
    elapsed = int((time.perf_counter() - t0) * 1000)
    if not isinstance(data, list) or not data:
        raise RuntimeError("Apify returned empty dataset")
    item = data[0]
    html = item.get("html") or item.get("text") or ""
    final_url = item.get("url") or url
    if not html:
        raise RuntimeError("Apify item had no HTML")
    return (html, final_url, elapsed)


def fetch_page(url: str, *, prefer_apify: bool = True) -> tuple[str, str, int, str]:
    """Fetch HTML for the URL. Returns (html, final_url, elapsed_ms, via)."""
    if prefer_apify and APIFY_TOKEN:
        try:
            html, final, ms = _fetch_via_apify(url)
            return html, final, ms, "apify"
        except Exception as e:
            print(f"[aeo] apify fetch failed, falling back: {e}")
    html, final, ms = _fetch_direct(url)
    return html, final, ms, "direct"


# ── Parser shim ─────────────────────────────────────────────────────


class _Parsed:
    """Minimal interface used by the scorers. Wraps bs4 if available.

    Caches expensive accessors on first call. ``visible_text()`` mutates
    bs4's tree (it decomposes <script>/<style>), so we must extract
    ``jsonld()`` first or cache the answer up-front. We cache lazily.
    """

    def __init__(self, html: str) -> None:
        self._html = html or ""
        if _HAS_BS4:
            self._soup = BeautifulSoup(self._html, "html.parser")
        else:
            self._soup = None
        self._jsonld_cache: list[dict] | None = None
        self._visible_text_cache: str | None = None

    @property
    def html(self) -> str:
        return self._html

    def title(self) -> str:
        if self._soup is not None:
            t = self._soup.find("title")
            return (t.get_text() if t else "").strip()
        m = re.search(r"<title[^>]*>(.*?)</title>", self._html, re.I | re.S)
        return (m.group(1).strip() if m else "")

    def meta(self, name: str) -> str:
        if self._soup is not None:
            tag = self._soup.find("meta", attrs={"name": re.compile(rf"^{re.escape(name)}$", re.I)})
            if tag and tag.get("content"):
                return tag["content"].strip()
            tag = self._soup.find("meta", attrs={"property": re.compile(rf"^{re.escape(name)}$", re.I)})
            return (tag["content"].strip() if tag and tag.get("content") else "")
        m = re.search(
            rf"""<meta\s+[^>]*?(?:name|property)\s*=\s*["']{re.escape(name)}["'][^>]*content\s*=\s*["']([^"']+)["']""",
            self._html, re.I)
        return m.group(1).strip() if m else ""

    def headings(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {f"h{i}": [] for i in range(1, 7)}
        if self._soup is not None:
            for i in range(1, 7):
                out[f"h{i}"] = [h.get_text(strip=True) for h in self._soup.find_all(f"h{i}")]
        else:
            for i in range(1, 7):
                out[f"h{i}"] = [m.group(1).strip() for m in re.finditer(
                    rf"<h{i}[^>]*>(.*?)</h{i}>", self._html, re.I | re.S)]
        return out

    def links(self) -> list[tuple[str, str]]:
        results = []
        if self._soup is not None:
            for a in self._soup.find_all("a", href=True):
                results.append((a["href"], a.get_text(strip=True)))
        else:
            for m in re.finditer(r"""<a\s[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""", self._html, re.I | re.S):
                results.append((m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()))
        return results

    def jsonld(self) -> list[dict]:
        if self._jsonld_cache is not None:
            return list(self._jsonld_cache)
        chunks: list[dict] = []
        if self._soup is not None:
            for s in self._soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(s.string or "")
                except Exception:
                    continue
                if isinstance(data, list):
                    chunks.extend(d for d in data if isinstance(d, dict))
                elif isinstance(data, dict):
                    chunks.append(data)
        else:
            for m in re.finditer(
                r"""<script[^>]*type=["']application/ld\+json["'][^>]*>(.*?)</script>""",
                self._html, re.I | re.S):
                try:
                    data = json.loads(m.group(1))
                except Exception:
                    continue
                if isinstance(data, list):
                    chunks.extend(d for d in data if isinstance(d, dict))
                elif isinstance(data, dict):
                    chunks.append(data)
        self._jsonld_cache = chunks
        return list(chunks)

    def visible_text(self) -> str:
        if self._visible_text_cache is not None:
            return self._visible_text_cache
        # Ensure JSON-LD is cached before we mutate the soup
        if self._jsonld_cache is None:
            self.jsonld()
        if self._soup is not None:
            for tag in self._soup(["script", "style", "noscript"]):
                tag.decompose()
            text = self._soup.get_text(" ", strip=True)
        else:
            text = re.sub(r"<script[^>]*>.*?</script>", " ", self._html, flags=re.I | re.S)
            text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        self._visible_text_cache = text
        return text


# ── Scorers ─────────────────────────────────────────────────────────


def _score_seo(parsed: _Parsed, final_url: str) -> CategoryScore:
    cat = CategoryScore(name="SEO", score=0)
    points = 0

    title = parsed.title()
    if title:
        points += 10
        if 30 <= len(title) <= 65:
            points += 5
        else:
            cat.add("medium",
                f"Title is {len(title)} chars — sweet spot is 30-65.",
                "Trim to under 65 characters so it doesn't get cut in Google SERPs.")
    else:
        cat.add("critical", "No <title> tag found.", "Add a unique, descriptive <title>.")

    desc = parsed.meta("description")
    if desc:
        points += 10
        if 70 <= len(desc) <= 165:
            points += 5
        else:
            cat.add("medium",
                f"Meta description is {len(desc)} chars — aim for 70-165.",
                "Rewrite to fit Google's snippet width.")
    else:
        cat.add("high", "Missing meta description.",
            "Write a 70-165 char summary that includes your primary keyword.")

    canonical = parsed.meta("canonical")
    if not canonical and parsed._soup is not None:
        link = parsed._soup.find("link", rel="canonical")
        canonical = (link.get("href") if link else "") or ""
    if canonical:
        points += 8
    else:
        cat.add("high", "No canonical link.",
            "Add <link rel='canonical'> so duplicates don't compete.")

    robots = parsed.meta("robots") or ""
    if "noindex" in robots.lower():
        cat.add("critical", f"<meta name='robots' content='{robots}'> blocks indexing.",
            "Remove the noindex if you want this page ranked.")
    else:
        points += 4

    headings = parsed.headings()
    h1 = headings.get("h1", [])
    if len(h1) == 1:
        points += 8
    elif len(h1) == 0:
        cat.add("high", "No <h1> on page.", "Every page needs exactly one H1.")
    else:
        cat.add("medium", f"Found {len(h1)} H1 tags.", "Keep it to one H1 per page.")
    if any(headings.get(f"h{i}") for i in (2, 3)):
        points += 4
    else:
        cat.add("medium", "No H2/H3 headings.", "Sub-headings help Google understand structure.")

    og_title = parsed.meta("og:title")
    og_desc = parsed.meta("og:description")
    og_img = parsed.meta("og:image")
    if og_title and og_desc and og_img:
        points += 10
    else:
        missing = [tag for tag, val in [("og:title", og_title), ("og:description", og_desc), ("og:image", og_img)] if not val]
        cat.add("medium", f"Open Graph missing: {', '.join(missing)}.",
            "Without OG tags your link previews on social platforms look broken.")

    tw_card = parsed.meta("twitter:card")
    if tw_card:
        points += 4
    else:
        cat.add("low", "No twitter:card meta tag.",
            "Add 'summary_large_image' for nicer Twitter/X previews.")

    # JSON-LD schema (any)
    schema_types = []
    for chunk in parsed.jsonld():
        t = chunk.get("@type")
        if isinstance(t, list):
            schema_types.extend(t)
        elif t:
            schema_types.append(t)
    if schema_types:
        points += 12
    else:
        cat.add("high", "No JSON-LD structured data.",
            "Add Organization + WebSite schema to every page.")

    # Internal vs external links
    links = parsed.links()
    if links:
        host = urlparse(final_url).netloc
        internal = sum(1 for href, _ in links if not href.startswith("http") or urlparse(urljoin(final_url, href)).netloc == host)
        external = sum(1 for href, _ in links if href.startswith("http") and urlparse(href).netloc and urlparse(href).netloc != host)
        if internal >= 3:
            points += 6
        else:
            cat.add("medium", f"Only {internal} internal links.", "Aim for ≥3 internal links to nearby pages.")
        if external >= 1:
            points += 4
        else:
            cat.add("low", "No external links.", "Linking out to authoritative sources helps E-E-A-T.")

    # Word count
    text = parsed.visible_text()
    words = len(text.split())
    if words >= 600:
        points += 10
    elif words >= 250:
        points += 5
        cat.add("medium", f"Thin content ({words} words).",
            "Pages under 600 words rarely rank for competitive queries.")
    else:
        cat.add("high", f"Very thin content ({words} words).",
            "Add 600+ words of useful information.")

    cat.score = max(0, min(100, points))
    return cat


def _score_aeo(parsed: _Parsed) -> CategoryScore:
    cat = CategoryScore(name="AEO", score=0)
    points = 0

    text = parsed.visible_text()
    text_lower = text.lower()

    # Q&A patterns: questions ending in '?' followed by an answer
    questions = re.findall(r"([^.?!]{8,140}\?)", text)
    if len(questions) >= 3:
        points += 18
    elif len(questions) >= 1:
        points += 8
        cat.add("medium", f"Only {len(questions)} question patterns detected.",
            "Answer engines (ChatGPT, Perplexity, Google AI Overviews) love clear Q&A pairs.")
    else:
        cat.add("high", "No question-format content found.",
            "Add an FAQ section. Use literal questions as H2/H3 headings.")

    # FAQ / HowTo schema
    schema_types = []
    for chunk in parsed.jsonld():
        t = chunk.get("@type")
        if isinstance(t, list):
            schema_types.extend([str(x) for x in t])
        elif t:
            schema_types.append(str(t))
    has_faq = any("FAQ" in t for t in schema_types)
    has_howto = any("HowTo" in t for t in schema_types)
    if has_faq:
        points += 18
    else:
        cat.add("high", "No FAQPage schema.",
            "Add FAQPage JSON-LD. This is the #1 way to win AI Overview citations.")
    if has_howto:
        points += 8

    # Definition / TL;DR patterns
    definitions = re.findall(r"\b(is|are|means|refers to|defined as)\b", text_lower)
    if len(definitions) >= 5:
        points += 8
    else:
        cat.add("medium", "Few definitional sentences.",
            "Open paragraphs with 'X is Y'-style sentences — they get extracted as featured snippets.")

    # Lists and tables
    list_count = parsed.html.count("<ul") + parsed.html.count("<ol")
    table_count = parsed.html.count("<table")
    if list_count >= 2:
        points += 10
    else:
        cat.add("medium", f"Only {list_count} lists on page.",
            "Bullet/numbered lists are heavily favored in featured-snippet selection.")
    if table_count >= 1:
        points += 6

    # Author / byline (E-E-A-T)
    if re.search(r"\b(author|by|written by|reviewed by)\b", text_lower) or \
       any("author" in (str(c.get("@type") or "")).lower() for c in parsed.jsonld()):
        points += 10
    else:
        cat.add("medium", "No author/byline detected.",
            "Add author info + Person schema. AI engines weight content with named experts higher.")

    # Date / updated
    if re.search(r"updated|last updated|published", text_lower) or parsed.meta("article:published_time"):
        points += 6
    else:
        cat.add("low", "No publication/update date.",
            "Add 'Last updated' so AI engines trust freshness.")

    # Citations
    if re.search(r"\b(source|according to|study|research)\b", text_lower):
        points += 8
    else:
        cat.add("low", "No citation patterns detected.",
            "Cite studies and authoritative sources with explicit names — AI loves cite-worthy claims.")

    # Numeric / statistic density
    stats = re.findall(r"\d{2,}\s*%|\$\d+|\b\d+(?:,\d{3})+\b", text)
    if len(stats) >= 3:
        points += 8
    else:
        cat.add("low", "Low statistic density.",
            "Concrete numbers (%, $, counts) get cited more than vague claims.")

    cat.score = max(0, min(100, points))
    return cat


def _score_geo(parsed: _Parsed) -> CategoryScore:
    cat = CategoryScore(name="GEO", score=0)
    points = 0

    text = parsed.visible_text()
    text_lower = text.lower()

    # Entity coverage (capitalized noun phrases)
    entities = re.findall(r"\b([A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]+){0,3})\b", text)
    unique_entities = set(entities)
    if len(unique_entities) >= 25:
        points += 15
    elif len(unique_entities) >= 10:
        points += 8
        cat.add("medium", f"Only {len(unique_entities)} distinct named entities.",
            "More entity coverage = more chances of being cited in generative answers.")
    else:
        cat.add("high", "Very few named entities.",
            "Reference real people, products, places, and brands.")

    # Schema.org entity hooks (Organization, Product, Person, etc.)
    schema_types = []
    for chunk in parsed.jsonld():
        t = chunk.get("@type")
        if isinstance(t, list):
            schema_types.extend([str(x) for x in t])
        elif t:
            schema_types.append(str(t))
    high_value = {"Organization", "Product", "Person", "LocalBusiness", "SoftwareApplication", "Article", "BlogPosting", "Review"}
    matched = [t for t in schema_types if t in high_value]
    if matched:
        points += 14
    else:
        cat.add("high", "No high-value entity schema (Organization/Product/Article/etc).",
            "Add Organization + (Product|Article|Service) schema.")

    # Citations / sameAs (entity disambiguation)
    has_same_as = any(c.get("sameAs") for c in parsed.jsonld())
    if has_same_as:
        points += 10
    else:
        cat.add("medium", "No sameAs links in schema.",
            "Add sameAs URLs to Wikipedia, Crunchbase, LinkedIn — these are critical for entity resolution.")

    # Clear noun + verb sentence density
    sentences = re.split(r"[.!?]+", text)
    short_clean = sum(1 for s in sentences if 6 <= len(s.split()) <= 28)
    if short_clean >= 30:
        points += 12
    elif short_clean >= 10:
        points += 6
        cat.add("low", "Few short, declarative sentences.",
            "AI extractors prefer sentences under 28 words.")
    else:
        cat.add("medium", "Very few clean declarative sentences.",
            "Long, convoluted prose gets ignored by extractors.")

    # Factual hooks: "X is Y", "X was founded in Y", named relationships
    facts = re.findall(r"\b[A-Z][a-z]+\s+(?:is|was|founded|launched|created|invented|owns|operates)\b", text)
    if len(facts) >= 5:
        points += 10
    else:
        cat.add("medium", "Low factual statement density.",
            "Make explicit factual claims with named subjects.")

    # First-person opinion vs third-person fact balance
    first_person = len(re.findall(r"\b(I|we|our|us)\b", text))
    third_person_fact = len(facts)
    if third_person_fact >= first_person:
        points += 8
    else:
        cat.add("low", "Mostly first-person voice.",
            "Generative engines extract third-person factual claims more than 'we believe' statements.")

    # Hreflang / multilingual
    if "hreflang" in parsed.html:
        points += 6

    # Author + organization schema cross-link
    has_author = any("Person" in (str(c.get("@type") or "")) for c in parsed.jsonld())
    if has_author:
        points += 10
    else:
        cat.add("medium", "No Person schema for author.",
            "Establish your author entity with Person schema + sameAs to LinkedIn, ORCID.")

    cat.score = max(0, min(100, points))
    return cat


def _build_recommendations(seo: CategoryScore, aeo: CategoryScore, geo: CategoryScore) -> list[str]:
    recs: list[str] = []
    sevs = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings: list[tuple[int, int, str, dict]] = []
    for idx, cat in enumerate((seo, aeo, geo)):
        for f in cat.findings:
            # Tie-break on (severity, category-order, finding-index) — never compare the dict itself
            findings.append((sevs.get(f["severity"], 3), idx, cat.name, f))
    findings.sort(key=lambda t: (t[0], t[1]))
    for _, _, cat_name, f in findings[:8]:
        msg = f"[{cat_name}] {f['message']}"
        if f.get("hint"):
            msg += f" → {f['hint']}"
        recs.append(msg)
    return recs


def analyze(url: str, *, prefer_apify: bool = True) -> AnalysisResult:
    """Run the full analyzer against a URL."""
    normalized = _normalize_url(url)
    if not normalized:
        raise ValueError("URL is required")
    html, final_url, fetch_ms, via = fetch_page(normalized, prefer_apify=prefer_apify)
    parsed = _Parsed(html)

    seo = _score_seo(parsed, final_url)
    aeo = _score_aeo(parsed)
    geo = _score_geo(parsed)
    overall = round((seo.score + aeo.score + geo.score) / 3)

    page_meta = {
        "title": parsed.title(),
        "description": parsed.meta("description"),
        "h1": parsed.headings().get("h1", []),
        "word_count": len(parsed.visible_text().split()),
        "schema_types": sorted({
            (str(t) if not isinstance(t, list) else ",".join(str(x) for x in t))
            for t in (c.get("@type") for c in parsed.jsonld()) if t
        }),
    }

    return AnalysisResult(
        url=normalized,
        final_url=final_url,
        fetched_at=time.time(),
        fetch_ms=fetch_ms,
        fetched_via=via,
        seo=seo,
        aeo=aeo,
        geo=geo,
        overall=overall,
        page_meta=page_meta,
        recommendations=_build_recommendations(seo, aeo, geo),
    )


# ── Flask routes ────────────────────────────────────────────────────


@aeo_bp.route("/tools/aeo-checker")
def aeo_checker_page():
    """Public AEO checker — designed to rank for 'AEO checker', 'GEO score',
    'AI search optimizer'. Open to guests for lead-gen."""
    return render_template("aeo_checker.html")


@aeo_bp.route("/api/aeo/analyze", methods=["POST"])
def aeo_analyze():
    """Run the analyzer. Returns JSON. Rate-limited at the App level."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"status": "error", "message": "url is required"}), 400
    try:
        result = analyze(url, prefer_apify=bool(data.get("use_apify", True)))
    except requests.HTTPError as e:
        return jsonify({"status": "error", "message": f"Could not fetch URL: HTTP {e.response.status_code}"}), 502
    except requests.RequestException as e:
        return jsonify({"status": "error", "message": f"Could not fetch URL: {e}"}), 502
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Analyzer failed: {e}"}), 500
    return jsonify({"status": "ok", **result.to_dict()})
