"""The sitemap, the noindex rule, and internal linking.

All three failures these cover are silent. A noindexed URL in a sitemap
looks exactly like a working one, an orphan page looks exactly like a linked
one, and both were found by an external crawler months after they shipped
rather than by anything in this repo.

Background: the signed-in product surface was added to ``_NOINDEX_EXACT``
with a good rationale, but nobody removed the same paths from
``_SITEMAP_ENTRIES``. The result was 22 URLs where the sitemap asked Google
to index a page and the X-Robots-Tag header told it not to.
"""

from __future__ import annotations

import re

import pytest

import App as app_module

#: Every comparison page. These rank for "<competitor> alternative", so an
#: orphan among them is lost traffic rather than a tidiness problem.
COMPARE_SLUGS = ("notion", "myhomework", "mystudylife", "quizlet", "turbo-ai")


@pytest.fixture
def client():
    return app_module.app.test_client()


def sitemap_paths(client) -> list[str]:
    xml = client.get("/sitemap.xml").get_data(as_text=True)
    locs = re.findall(r"<loc>(.*?)</loc>", xml)
    base = app_module.APP_BASE_URL.rstrip("/")
    return [u[len(base):] or "/" for u in locs]


# ── The sitemap must not contradict the noindex rule ────────────────


def test_no_sitemap_url_is_noindexed(client):
    """The regression guard. Add a path to _NOINDEX_EXACT without removing
    it from _SITEMAP_ENTRIES and this fails."""
    offenders = [p for p in sitemap_paths(client) if app_module._should_noindex(p)]
    assert not offenders, (
        "these URLs are in sitemap.xml but serve noindex: " + ", ".join(offenders)
    )


def test_the_entry_list_itself_names_no_noindexed_path():
    """Checks _SITEMAP_ENTRIES raw, not the rendered sitemap.

    _indexable_sitemap_entries filters these out at request time, so a bad
    entry never reaches production — but that also means the rendered output
    looks perfect while the list quietly rots. This is the test that fails
    when someone adds a path to _NOINDEX_EXACT and forgets the other list.
    """
    offenders = [
        path for path, *_ in app_module._SITEMAP_ENTRIES
        if app_module._should_noindex(path)
    ]
    assert not offenders, (
        "_SITEMAP_ENTRIES lists paths that serve noindex: " + ", ".join(offenders)
    )


def test_the_signed_in_surface_is_absent_from_the_sitemap(client):
    paths = set(sitemap_paths(client))
    for path in ("/dashboard", "/scheduler", "/gradebook", "/grades",
                 "/streak", "/memories", "/classes", "/priority", "/tests",
                 "/lessons", "/groups", "/meetings", "/writing", "/math",
                 "/extractor", "/study", "/study-and-learn", "/grademodel"):
        assert path not in paths, f"{path} is a signed-in page and should not be listed"


def test_the_signed_in_surface_still_sends_noindex(client):
    """The other half: removing them from the sitemap must not be mistaken
    for making them indexable."""
    for path in ("/dashboard", "/scheduler", "/gradebook", "/streak"):
        header = client.get(path).headers.get("X-Robots-Tag", "")
        assert "noindex" in header.lower(), f"{path} -> {header!r}"


def test_the_public_content_pages_are_indexable_and_listed(client):
    """/tutor, /olympiad, /library and /focus are full public pages with
    hand-written meta descriptions, not signed-in shells. An earlier pass
    swept them into the noindex list, which threw away the only pages in
    that group worth ranking."""
    paths = set(sitemap_paths(client))
    for path in ("/tutor", "/olympiad", "/library", "/focus"):
        assert not app_module._should_noindex(path), f"{path} should be indexable"
        assert path in paths, f"{path} should be in the sitemap"
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        assert "noindex" not in response.headers.get("X-Robots-Tag", "").lower()


def test_the_sitemap_is_well_formed_and_not_empty(client):
    import xml.etree.ElementTree as ET

    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    root = ET.fromstring(response.get_data())
    urls = root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url")
    assert len(urls) > 20, f"only {len(urls)} URLs — did a filter go too far?"


def test_indexnow_and_the_sitemap_agree(client):
    """IndexNow pushes URLs at search engines directly. Submitting a
    noindexed URL is a worse version of the same mistake."""
    with app_module.app.test_request_context():
        submitted = app_module._indexnow_sitemap_urls()
    base = app_module.APP_BASE_URL.rstrip("/")
    offenders = [
        u for u in submitted if app_module._should_noindex(u[len(base):] or "/")
    ]
    assert not offenders, "IndexNow would submit noindexed URLs: " + ", ".join(offenders)


# ── Internal linking ────────────────────────────────────────────────


def test_every_comparison_page_has_an_internal_link_in(client):
    """`/compare/intelliplan-vs-mystudylife` was linked from nowhere except
    itself: in the sitemap, reachable by URL, and unreachable by crawling."""
    landing = client.get("/").get_data(as_text=True)
    hub = client.get("/compare").get_data(as_text=True)
    blog = client.get("/blog").get_data(as_text=True)

    for slug in COMPARE_SLUGS:
        href = f"/compare/intelliplan-vs-{slug}"
        linked_from = [
            name
            for name, body in (("/", landing), ("/compare", hub), ("/blog", blog))
            if href in body
        ]
        assert linked_from, f"{href} has no internal links in — it is orphaned"


def test_the_compare_hub_links_all_of_its_spokes(client):
    """A hub that does not link its own spokes is not a hub."""
    hub = client.get("/compare").get_data(as_text=True)
    missing = [
        s for s in COMPARE_SLUGS if f"/compare/intelliplan-vs-{s}" not in hub
    ]
    assert not missing, f"/compare does not link: {missing}"


def test_every_comparison_page_still_renders(client):
    for slug in COMPARE_SLUGS:
        response = client.get(f"/compare/intelliplan-vs-{slug}")
        assert response.status_code == 200, f"{slug} -> {response.status_code}"


def test_the_comparison_pages_are_in_the_sitemap(client):
    paths = set(sitemap_paths(client))
    for slug in COMPARE_SLUGS:
        assert f"/compare/intelliplan-vs-{slug}" in paths, slug
