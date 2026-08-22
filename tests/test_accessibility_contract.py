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


# ── WCAG 2.2 AA contracts ────────────────────────────────────────────
#
# Added alongside docs/accessibility/POLICY.md. Each of these encodes a
# failure that has actually shipped in this codebase, not a hypothetical one.

#: Action controls. Anchors are deliberately excluded: a link inside
#: announced prose ("add free hours in Settings") reads naturally and is
#: still reachable by link navigation, so it does not have the failure shape
#: this rule exists to catch — a control that appears, gets read out as
#: prose, and never receives focus. A button does.
_INTERACTIVE = re.compile(r"<(button|select|textarea|input)", re.I)

#: Attributes that make an otherwise-static element a live region.
_LIVE_REGION = re.compile(
    r'<(\w+)([^>]*\b(?:role="(?:status|alert)"|aria-live="(?:polite|assertive)")[^>]*)>',
    re.I,
)


def _tag_body(html: str, start: int, tag: str) -> str:
    """Crude innerHTML of the element opening at ``start``.

    Deliberately not a real parser: it walks to the matching close tag,
    counting nesting. Good enough to answer "does this region contain a
    button?" over server-rendered markup we control.
    """
    depth = 0
    pos = start
    open_re = re.compile(rf"<{tag}\b", re.I)
    close_re = re.compile(rf"</{tag}\s*>", re.I)
    while pos < len(html):
        nxt_open = open_re.search(html, pos)
        nxt_close = close_re.search(html, pos)
        if not nxt_close:
            return html[start:]
        if nxt_open and nxt_open.start() < nxt_close.start():
            depth += 1
            pos = nxt_open.end()
            continue
        if depth == 0:
            return html[start:nxt_close.start()]
        depth -= 1
        pos = nxt_close.end()
    return html[start:]


def _unnamed_buttons(html: str) -> list[str]:
    """Buttons a screen reader would announce as just "button"."""
    unnamed = []
    for match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", html, re.S | re.I):
        attrs, inner = match.group(1), match.group(2)
        if "aria-label" in attrs or "aria-labelledby" in attrs:
            continue
        if "aria-hidden" in attrs:
            continue
        # Text content counts as a name. Strip tags (an <svg> icon supplies
        # nothing) and see whether any words survive.
        words = re.sub(r"<[^>]+>", "", inner)
        words = re.sub(r"&[a-z]+;|&#\d+;", "", words, flags=re.I).strip()
        if words:
            continue
        unnamed.append(match.group(0)[:120])
    return unnamed


@pytest.mark.parametrize("path", APP_PAGES + PUBLIC_PAGES)
def test_every_button_has_an_accessible_name(client, path):
    """An icon-only button with no aria-label announces as "button".

    The inputs check above covers form fields; this covers the other half,
    which is where icon buttons live.
    """
    response = client.get(path)
    if response.status_code != 200:
        pytest.skip(f"{path} returned {response.status_code}")
    unnamed = _unnamed_buttons(response.get_data(as_text=True))
    assert not unnamed, f"{path} has unnamed button(s): {unnamed[:3]}"


@pytest.mark.parametrize("path", APP_PAGES)
def test_live_regions_do_not_contain_interactive_controls(client, path):
    """A status region holding buttons is announced wholesale.

    The user hears the button labels read at them as prose, and focus is
    still nowhere near the controls. The fix is always the same: the message
    is the live region, the controls are siblings outside it.
    """
    response = client.get(path)
    if response.status_code != 200:
        pytest.skip(f"{path} returned {response.status_code}")
    html = response.get_data(as_text=True)

    offenders = []
    for match in _LIVE_REGION.finditer(html):
        tag = match.group(1)
        body = _tag_body(html, match.end(), tag)
        if _INTERACTIVE.search(body):
            offenders.append(match.group(0)[:120])
    assert not offenders, (
        f"{path} has live region(s) containing controls: {offenders[:2]}"
    )


@pytest.mark.parametrize("path", APP_PAGES)
def test_aria_controls_point_at_real_elements(client, path):
    """A dangling aria-controls tells assistive tech about a relationship
    that does not exist, which is worse than declaring none."""
    response = client.get(path)
    if response.status_code != 200:
        pytest.skip(f"{path} returned {response.status_code}")
    html = response.get_data(as_text=True)

    dangling = []
    for match in re.finditer(r'aria-controls="([^"]+)"', html):
        for target in match.group(1).split():
            if not re.search(rf'id="{re.escape(target)}"', html):
                dangling.append(target)
    assert not dangling, f"{path} has aria-controls pointing at nothing: {dangling[:3]}"


@pytest.mark.parametrize("path", APP_PAGES)
def test_toggle_buttons_declare_their_expanded_state(client, path):
    """A control that shows and hides something must say which it is doing.

    Anything with aria-controls is a disclosure by definition, so it needs a
    starting aria-expanded for a screen reader to announce.
    """
    response = client.get(path)
    if response.status_code != 200:
        pytest.skip(f"{path} returned {response.status_code}")
    html = response.get_data(as_text=True)

    silent = []
    for match in re.finditer(r"<button\b([^>]*)>", html, re.I):
        attrs = match.group(1)
        if "aria-controls" in attrs and "aria-expanded" not in attrs:
            silent.append(match.group(0)[:100])
    assert not silent, f"{path} has disclosure button(s) with no state: {silent[:2]}"


# ── Contrast ─────────────────────────────────────────────────────────


def _srgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio between two hex colours."""
    a, b = _relative_luminance(_srgb(foreground)), _relative_luminance(_srgb(background))
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


#: (theme, --bg-card, --accent) for every palette in ip-base.css.
#: Hardcoded rather than parsed: the point is to fail when someone adds a
#: theme without checking it, and parsing would silently skip a palette
#: written in a shape the parser did not expect.
THEME_PALETTES = [
    ("light",        "#ffffff", "#1a56db"),
    ("dark",         "#1c1c20", "#5b93f5"),
    ("ocean-light",  "#ffffff", "#0369a1"),
    ("ocean-dark",   "#111820", "#38bdf8"),
    ("forest-light", "#ffffff", "#15803d"),
    ("forest-dark",  "#112016", "#4ade80"),
    ("sunset-light", "#ffffff", "#c2410c"),
    ("sunset-dark",  "#201510", "#fb923c"),
    ("rose-light",   "#ffffff", "#be185d"),
    ("rose-dark",    "#201020", "#f472b6"),
    ("violet-dark",  "#12101e", "#a78bfa"),
    ("sand-light",   "#ffffff", "#78552e"),
    ("sand-dark",    "#201a10", "#c8a17a"),
]

#: WCAG AA for text below 18.66px bold / 24px regular.
AA_SMALL_TEXT = 4.5


def test_the_contrast_helper_matches_known_wcag_values():
    """Guards the guard. If this drifts, every ratio below is meaningless."""
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)
    assert contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.02)


@pytest.mark.parametrize("theme,card,accent", THEME_PALETTES)
def test_accent_and_card_background_stay_legible_in_every_theme(theme, card, accent):
    """The button-label pairing the policy mandates.

    ``--accent`` and ``--bg-card`` sit at opposite ends of each theme's
    lightness range, so pairing them is legible everywhere. This test exists
    because the obvious alternative — hardcoding ``#fff`` on the accent —
    fails in all six dark themes, bottoming out around 1.74:1, and looks
    completely fine to anyone developing in light mode.
    """
    ratio = contrast_ratio(card, accent)
    assert ratio >= AA_SMALL_TEXT, (
        f"{theme}: --bg-card {card} on --accent {accent} is {ratio:.2f}:1, "
        f"below the {AA_SMALL_TEXT}:1 floor for small text"
    )


@pytest.mark.parametrize("theme,card,accent", THEME_PALETTES)
def test_accent_is_legible_as_text_on_the_card(theme, card, accent):
    """The other direction: accent-coloured text on a card background.

    Used for the next-action eyebrow and for links throughout the app.
    """
    ratio = contrast_ratio(accent, card)
    assert ratio >= AA_SMALL_TEXT, (
        f"{theme}: --accent {accent} text on --bg-card {card} is {ratio:.2f}:1"
    )
