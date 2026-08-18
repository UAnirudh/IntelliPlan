"""Accessibility and indexing contracts that hold across every page.

These are regression tests, not a substitute for a real audit. They cover
the two failures that are easiest to reintroduce and hardest to notice:

  * A form control with no programmatic name. It looks fine, works fine
    with a mouse, and is unusable with a screen reader — the reader has
    nothing to announce but "edit text". A placeholder does not count: it
    is not a name, and it disappears the moment the field has content.

  * A signed-in app page advertising itself as indexable. Logged out it is
    a redirect to /login, and any that do render for a guest are thin by
    construction. Crawl budget spent there is budget not spent on the
    marketing pages that are actually meant to rank.

Both are checked against rendered HTML rather than templates, so a name
supplied by an include, a macro or a base template still counts.
"""

import re

import pytest

import App

#: The signed-in surface. Every one of these must be noindex.
APP_PAGES = [
    "/command-center", "/scheduler", "/gradebook", "/grademodel",
    "/settings", "/streak", "/pet", "/balance", "/memories",
    "/my-stats", "/classes", "/priority",
]

#: Public pages that must stay indexable — the inverse guard, so a
#: future noindex sweep cannot quietly delist the marketing site.
PUBLIC_PAGES = ["/", "/pricing", "/about"]

#: Input types that carry their own meaning or are never user-facing.
_UNNAMEABLE = re.compile(r'type="(hidden|submit|button|image)"')


@pytest.fixture(scope="module")
def client():
    App.app.config["WTF_CSRF_ENABLED"] = False
    c = App.app.test_client()
    with App.app.app_context():
        user = App.User.query.first()
        if user is None:
            pytest.skip("no user in the development database")
        uid = user.id
    with c.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    return c


def _unnamed_inputs(html: str) -> list[str]:
    """Inputs with no accessible name, by any of the four valid routes."""
    explicit = set(re.findall(r'<label[^>]*for="([^"]+)"', html))

    # <label><input …></label> — implicit association, equally valid.
    wrapped = set()
    for label in re.finditer(r"<label\b[^>]*>(.*?)</label>", html, re.S | re.I):
        for inp in re.finditer(r"<input\b([^>]*)>", label.group(1), re.I):
            found = re.search(r'id="([^"]+)"', inp.group(1))
            wrapped.add(found.group(1) if found else inp.group(0)[:60])

    unnamed = []
    for match in re.finditer(r"<input\b([^>]*)>", html, re.I):
        attrs = match.group(1)
        if _UNNAMEABLE.search(attrs):
            continue
        if "aria-label" in attrs or "aria-labelledby" in attrs:
            continue
        found = re.search(r'id="([^"]+)"', attrs)
        key = found.group(1) if found else match.group(0)[:60]
        if found and found.group(1) in explicit:
            continue
        if key in wrapped:
            continue
        unnamed.append(match.group(0)[:100])
    return unnamed


def _robots(html: str) -> str:
    tag = re.search(r'<meta name="robots"[^>]*>', html)
    return tag.group(0) if tag else ""


@pytest.mark.parametrize("path", APP_PAGES + PUBLIC_PAGES)
def test_every_form_control_has_an_accessible_name(client, path):
    response = client.get(path)
    if response.status_code != 200:
        pytest.skip(f"{path} returned {response.status_code}")
    unnamed = _unnamed_inputs(response.get_data(as_text=True))
    assert not unnamed, f"{path} has unnamed input(s): {unnamed[:3]}"


@pytest.mark.parametrize("path", APP_PAGES)
def test_signed_in_pages_are_not_indexable(client, path):
    response = client.get(path)
    if response.status_code != 200:
        pytest.skip(f"{path} returned {response.status_code}")
    assert "noindex" in _robots(response.get_data(as_text=True)), (
        f"{path} is advertising itself as indexable"
    )


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_marketing_pages_stay_indexable(client, path):
    response = client.get(path)
    if response.status_code != 200:
        pytest.skip(f"{path} returned {response.status_code}")
    assert "noindex" not in _robots(response.get_data(as_text=True)), (
        f"{path} is a marketing page and must not be noindex"
    )


def test_the_skip_link_points_at_a_real_landmark(client):
    """It is the first thing a keyboard user reaches. If its target stops
    existing, the link silently does nothing and there is no other way to
    get past the nav."""
    html = client.get("/").get_data(as_text=True)
    # Matched without assuming attribute order — the markup writes href
    # first, and an assertion that depends on which attribute comes first
    # tests the author's typing habits rather than the page.
    link = None
    for tag in re.finditer(r"<a[^>]*>", html):
        if "a11y-skip" in tag.group(0):
            link = re.search(r'href="#([^"]+)"', tag.group(0))
            break
    assert link, "the skip link is gone"
    target = link.group(1)
    assert re.search(rf'id="{re.escape(target)}"', html), (
        f"skip link points at #{target}, which is not on the page"
    )
