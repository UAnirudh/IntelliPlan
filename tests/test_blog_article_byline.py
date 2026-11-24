"""Each guide renders exactly one byline, and it agrees with the registry.

The byline was rendered twice. ``blog_article.html`` printed the article's
hand-written reading time while a script in ``base.html`` counted the body
words itself and injected a second badge, so one page showed "4 min read"
directly above "6 min read". The fix was to count once, on the server, and to
skip the script when ``.blog-meta`` is already on the page.

Only the server-rendered half is visible to a test client: the script's work
happens in a browser. So the assertion here is that the template emits one
byline and that it carries the registry's own date and word count — the two
values that used to drift.
"""

from __future__ import annotations

import re

import pytest

import App as app_module
import blog_articles

#: The byline paragraph, and any "N min read" text anywhere on the page.
BYLINE_RE = re.compile(r'<p class="blog-meta">(.*?)</p>', re.S)
READING_TIME_RE = re.compile(r"\b\d+ min read\b")


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.mark.parametrize("slug", [article["slug"] for article in blog_articles.ARTICLES])
def test_article_renders_exactly_one_byline(client, slug):
    response = client.get(f"/blog/{slug}")
    assert response.status_code == 200, f"{slug} -> {response.status_code}"
    body = response.get_data(as_text=True)

    bylines = BYLINE_RE.findall(body)
    assert len(bylines) == 1, f"{slug} renders {len(bylines)} .blog-meta bylines"

    reading_times = READING_TIME_RE.findall(body)
    assert len(reading_times) == 1, (
        f"{slug} shows {len(reading_times)} reading times: {reading_times}"
    )


@pytest.mark.parametrize("slug", [article["slug"] for article in blog_articles.ARTICLES])
def test_byline_matches_the_registry(client, slug):
    """A stale hand-written ``reading_time`` is what produced the mismatch."""
    article = blog_articles.get(slug)
    expected = (
        f"{blog_articles.display_date(article['date'])} &middot; "
        f"{blog_articles.reading_minutes(article)} min read"
    )
    body = client.get(f"/blog/{slug}").get_data(as_text=True)
    (byline,) = BYLINE_RE.findall(body)
    assert byline.strip() == expected, f"{slug}: {byline!r} != {expected!r}"


def test_no_legacy_guide_renders_the_registry_byline(client):
    """The older standalone pages get their badge from the script instead.

    If one ever starts rendering ``.blog-meta`` as well, it has a duplicate
    byline for the same reason the registry articles did.
    """
    offenders = []
    for slug in blog_articles.LEGACY_TITLES:
        body = client.get(f"/blog/{slug}").get_data(as_text=True)
        if BYLINE_RE.search(body):
            offenders.append(slug)
    assert not offenders, "standalone guides rendering a .blog-meta byline: " + ", ".join(offenders)
