"""The block-resources endpoint and linked material.

study_resources is tested on its own. These cover the parts only the route
has: that a panel which cannot suggest anything still opens, that a guest
and a signed-in student each see their own linked material and nobody
else's, and that a stored link cannot be a javascript: URL.
"""

from __future__ import annotations

import json

import pytest

import App
import study_resources as sr


CALC_BLOCK = {
    "assignment": "Problem set 4: implicit differentiation",
    "course": "AP Calculus",
    "duration_minutes": 45,
}


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


@pytest.fixture(autouse=True)
def no_live_ai(monkeypatch):
    """Default to AI off so these test the route, not a model."""
    monkeypatch.setattr(App, "_resource_chat", lambda: None)


def ask(client, block=None):
    return client.post("/api/block/resources",
                       json={"block": block if block is not None else CALC_BLOCK})


# ── The panel always opens ───────────────────────────────────────────


def test_a_block_with_nothing_to_suggest_still_answers(client):
    r = ask(client)
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok", "resources": []}


def test_a_break_gets_no_resources(client):
    r = ask(client, {"assignment": "Break", "is_break": True})
    assert r.get_json()["resources"] == []


def test_a_missing_block_does_not_500(client):
    assert client.post("/api/block/resources", json={}).status_code == 200


def test_a_junk_block_is_rejected_cleanly(client):
    r = client.post("/api/block/resources", json={"block": "not a dict"})
    assert r.status_code == 400


def test_an_exception_inside_the_engine_yields_an_empty_panel(client, monkeypatch):
    """A modal that fails to render is the feature not existing. An empty
    one is a small loss."""
    def boom(*a, **k):
        raise RuntimeError("engine exploded")
    monkeypatch.setattr(App.study_resources, "resources_for_block", boom)
    r = ask(client)
    assert r.status_code == 200
    assert r.get_json()["resources"] == []


def test_links_in_the_assignment_come_back(client):
    r = ask(client, dict(CALC_BLOCK, description="Read https://example.edu/ch4.pdf"))
    rows = r.get_json()["resources"]
    assert [x["url"] for x in rows] == ["https://example.edu/ch4.pdf"]


# ── With a model answering ───────────────────────────────────────────


def test_a_models_pick_becomes_a_real_url(client, monkeypatch):
    monkeypatch.setattr(App, "_resource_chat", lambda: (lambda m: json.dumps(
        {"resources": [{"provider": "khan", "query": "implicit differentiation",
                        "title": "Implicit differentiation", "why": "Practice."}]})))
    rows = ask(client).get_json()["resources"]
    assert len(rows) == 1
    assert rows[0]["url"].startswith("https://www.khanacademy.org/search?")


def test_a_model_that_invents_a_provider_yields_nothing(client, monkeypatch):
    """The endpoint must not be the place a hallucinated link escapes."""
    monkeypatch.setattr(App, "_resource_chat", lambda: (lambda m: json.dumps(
        {"resources": [{"provider": "mathsite.invalid", "query": "x",
                        "title": "T", "why": ""}]})))
    assert ask(client).get_json()["resources"] == []


def test_an_ai_failure_still_returns_the_students_own_links(client, monkeypatch):
    def boom(m):
        raise RuntimeError("quota")
    monkeypatch.setattr(App, "_resource_chat", lambda: boom)
    rows = ask(client, dict(CALC_BLOCK,
                            description="Read https://example.edu/ch4.pdf")).get_json()["resources"]
    assert [x["url"] for x in rows] == ["https://example.edu/ch4.pdf"]


# ── Linked material ──────────────────────────────────────────────────


def test_a_guest_can_link_material_and_see_it_on_a_block(client):
    r = client.post("/api/resource-accounts", json={
        "provider": "quizlet", "label": "Unit 4 vocab",
        "url": "https://quizlet.com/123/unit-4", "course": "AP Calculus"})
    assert r.status_code == 200

    rows = ask(client).get_json()["resources"]
    assert rows[0]["title"] == "Unit 4 vocab"
    assert rows[0]["source"] == "linked_account"


def test_linked_material_for_another_course_does_not_surface(client):
    client.post("/api/resource-accounts", json={
        "provider": "quizlet", "label": "Cells",
        "url": "https://quizlet.com/1/cells", "course": "Biology"})
    assert ask(client).get_json()["resources"] == []


def test_material_linked_to_no_course_surfaces_everywhere(client):
    client.post("/api/resource-accounts", json={
        "provider": "khan", "label": "My Khan",
        "url": "https://khanacademy.org/profile/me"})
    assert len(ask(client).get_json()["resources"]) == 1


def test_a_bare_host_gets_a_scheme(client):
    client.post("/api/resource-accounts", json={
        "provider": "quizlet", "label": "S", "url": "quizlet.com/1/x"})
    acct = client.get("/api/resource-accounts").get_json()["accounts"][0]
    assert acct["url"].startswith("https://")


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
])
def test_a_script_url_cannot_be_stored(client, bad):
    """These are rendered as anchors in the panel, so storing one is
    stored XSS aimed at the student's own next click."""
    client.post("/api/resource-accounts", json={
        "provider": "quizlet", "label": "X", "url": bad})
    urls = [a["url"] for a in client.get("/api/resource-accounts").get_json()["accounts"]]
    for u in urls:
        assert u.lower().startswith(("http://", "https://")), u


def test_a_blank_url_is_rejected(client):
    r = client.post("/api/resource-accounts", json={"label": "X", "url": "  "})
    assert r.status_code == 400


def test_listing_offers_the_providers_we_can_link(client):
    data = client.get("/api/resource-accounts").get_json()
    assert {p["key"] for p in data["providers"]} == {p.key for p in sr.PROVIDERS}


def test_a_guest_can_remove_their_own_link(client):
    acct = client.post("/api/resource-accounts", json={
        "provider": "quizlet", "label": "S",
        "url": "https://quizlet.com/1/x"}).get_json()["account"]
    client.delete("/api/resource-accounts", json={"id": acct["id"]})
    assert client.get("/api/resource-accounts").get_json()["accounts"] == []


def test_one_guest_cannot_see_or_delete_anothers(client):
    """Guests are separated only by a session id, so this is the whole of
    the isolation between two strangers on the same deployment."""
    acct = client.post("/api/resource-accounts", json={
        "provider": "quizlet", "label": "Mine",
        "url": "https://quizlet.com/1/x"}).get_json()["account"]

    with App.app.test_client() as other:
        assert other.get("/api/resource-accounts").get_json()["accounts"] == []
        other.delete("/api/resource-accounts", json={"id": acct["id"]})

    assert len(client.get("/api/resource-accounts").get_json()["accounts"]) == 1, \
        "another visitor deleted this one's linked material"


# ── Signed in ────────────────────────────────────────────────────────


@pytest.fixture
def student(request):
    from App import ResourceAccount, User, db
    with App.app.app_context():
        User.query.filter(User.email.like("restest+%")).delete(synchronize_session=False)
        db.session.commit()
        u = User(email="restest+a@example.com", name="Resource Tester",
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode())
        db.session.add(u)
        db.session.commit()
        uid = u.id

    def cleanup():
        with App.app.app_context():
            ResourceAccount.query.filter_by(user_id=uid).delete()
            User.query.filter_by(id=uid).delete()
            db.session.commit()
    request.addfinalizer(cleanup)
    return uid


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def test_a_signed_in_students_material_is_stored_against_their_account(client, student):
    login(client, student)
    client.post("/api/resource-accounts", json={
        "provider": "quizlet", "label": "Unit 4",
        "url": "https://quizlet.com/1/x", "course": "AP Calculus"})
    with App.app.app_context():
        rows = App.ResourceAccount.query.filter_by(user_id=student).all()
        assert len(rows) == 1
        assert rows[0].course == "AP Calculus"


def test_a_signed_in_students_material_reaches_their_blocks(client, student):
    login(client, student)
    client.post("/api/resource-accounts", json={
        "provider": "quizlet", "label": "Unit 4",
        "url": "https://quizlet.com/1/x", "course": "AP Calculus"})
    assert ask(client).get_json()["resources"][0]["title"] == "Unit 4"


def test_a_signed_in_student_cannot_delete_someone_elses(client, student):
    from App import ResourceAccount, db
    with App.app.app_context():
        other = ResourceAccount(user_id=student + 99999, label="Theirs",
                                url="https://quizlet.com/9/x", provider="quizlet")
        db.session.add(other)
        db.session.commit()
        other_id = other.id

    login(client, student)
    client.delete("/api/resource-accounts", json={"id": other_id})
    with App.app.app_context():
        assert ResourceAccount.query.filter_by(id=other_id).first() is not None
        ResourceAccount.query.filter_by(id=other_id).delete()
        db.session.commit()
