"""On a phone, the hamburger drawer is the whole app menu.

The sidebar is desktop-only — ``mobile.css`` hides ``.app-side`` outright —
so on a phone the only way to anywhere is the drawer, which renders
``#mainNav``. That nav carried the six tabs the desktop top bar shows and
stopped there, which left Flashcards, Streak, My Pet, Balance, My Stats and
every tool page reachable on a laptop and reachable nowhere on a phone.
Reported as: "when you open the 3 lines you can only see a limited amount
of things, not everything is in there."

These tests pin the drawer against the two lists that define what the app
contains — the sidebar, and the command palette's own index — so the next
page added to either does not quietly go missing on phones again.
"""

import re

import pytest

import App
from App import User, db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            User.query.filter(User.email.like("drawer+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            User.query.filter(User.email.like("drawer+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


@pytest.fixture
def page(client):
    with App.app.app_context():
        u = User(email="drawer+a@example.com",
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                 name="Drawer Tester")
        db.session.add(u)
        db.session.commit()
    with App.app.app_context():
        uid = User.query.filter_by(email="drawer+a@example.com").first().id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return client.get("/scheduler").data.decode("utf-8", "ignore")


def drawer_markup(html):
    """Everything inside <nav id="mainNav"> — what the phone drawer shows."""
    start = html.find('id="mainNav"')
    assert start != -1, "the nav that becomes the phone drawer is gone"
    end = html.find("</nav>", start)
    return html[start:end]


def hrefs(markup):
    return set(re.findall(r'href="(/[^"#?]*)', markup))


def sidebar_markup(html):
    start = html.find('class="app-side')
    if start == -1:
        return ""
    return html[start:html.find("</aside>", start)]


# ── The drawer against the sidebar ───────────────────────────────────


def test_every_sidebar_destination_is_in_the_drawer(page):
    """The sidebar is display:none on a phone. Anything only in there is,
    on a phone, unreachable."""
    side = hrefs(sidebar_markup(page))
    assert side, "no sidebar rendered — this test is no longer testing anything"
    # "/" is the logo, which sits in the phone header beside the hamburger
    # and so is never behind the drawer.
    side.discard("/")
    missing = side - hrefs(drawer_markup(page))
    assert not missing, f"unreachable on a phone: {sorted(missing)}"


@pytest.mark.parametrize("path", [
    "/command-center", "/dashboard", "/scheduler", "/active", "/memories",
    "/gradebook", "/grades", "/study-and-learn", "/learn", "/flashcards",
    "/streak", "/pet", "/balance", "/my-stats", "/features", "/settings",
])
def test_the_named_destinations_are_all_there(page, path):
    """Spelled out so a regression names the page it dropped."""
    assert f'href="{path}"' in drawer_markup(page)


# ── The drawer against the command palette ───────────────────────────


def test_the_drawer_covers_the_command_palette_index(page):
    """The palette is keyboard-only, so on a phone it is not a fallback for
    anything the drawer leaves out."""
    palette = page[page.find("const NAV_ITEMS"):]
    palette = palette[:palette.find("];")]
    listed = set(re.findall(r"href:'(/[^'#?]*)", palette))
    assert listed, "the command palette index moved; this test needs updating"
    missing = listed - hrefs(drawer_markup(page))
    assert not missing, f"in the palette but not the drawer: {sorted(missing)}"


# ── Shape ────────────────────────────────────────────────────────────


def test_the_extra_groups_are_labelled(page):
    """Two dozen links in a flat list is a wall, not a menu."""
    markup = drawer_markup(page)
    for heading in ("Plan", "Study", "Progress", "Tools", "Connect"):
        assert f'class="nav-more-head">{heading}<' in markup


def test_the_desktop_bar_is_not_given_the_whole_list(page):
    """The extra groups are drawer-only: the desktop top bar has a sidebar
    beside it and room for six tabs, not thirty."""
    markup = drawer_markup(page)
    assert 'class="nav-group nav-more"' in markup
    css = open("static/css/ip-base.css", encoding="utf-8").read()
    assert ".nav-group.nav-more { display:none; }" in css
    assert ".nav-tab.nav-more-item { display:none; }" in css


def test_grades_and_the_grade_modeler_are_told_apart(page):
    """Both were labelled "Grades" once they sat in the same menu."""
    markup = drawer_markup(page)
    assert "Grade Modeler" in markup
    assert 'href="/grades"' in markup
