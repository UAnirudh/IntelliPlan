"""Replaying an offline write must not apply it twice.

The scenario these guard is the one an offline queue creates on purpose:
the client sends a mutation, the radio dies before the response arrives,
and on reconnect the queue sends the same request again. Without a ledger
the second send is a second write — a second streak credit, a second batch
of pet XP, a second row.

``/dismiss`` is the endpoint used throughout because it is the one with
real side effects beyond the row it writes, so "did it run twice?" is
observable rather than theoretical.
"""

from __future__ import annotations

import pytest

import App
from App import DismissedAssignment, User, db
from intelliplan.sync.models import OP_ID_HEADER, REPLAY_HEADER


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


@pytest.fixture
def user_id():
    email = "sync+ledger@example.com"
    with App.app.app_context():
        User.query.filter_by(email=email).delete(synchronize_session=False)
        db.session.commit()
        user = User(email=email,
                    password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                    name="Sync Tester")
        db.session.add(user)
        db.session.commit()
        uid = user.id
    yield uid
    with App.app.app_context():
        App.SyncOp.query.filter_by(user_id=uid).delete(synchronize_session=False)
        DismissedAssignment.query.filter_by(user_id=uid).delete(synchronize_session=False)
        User.query.filter_by(id=uid).delete(synchronize_session=False)
        db.session.commit()


def login(client, uid: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


def dismiss(client, title: str, op_id: str | None = None):
    headers = {OP_ID_HEADER: op_id} if op_id else {}
    return client.post("/dismiss", json={"title": title}, headers=headers)


# ── The replay contract ─────────────────────────────────────────────


def test_the_same_op_id_twice_writes_one_row(client, user_id):
    login(client, user_id)
    first = dismiss(client, "Lab report", op_id="op-alpha")
    second = dismiss(client, "Lab report", op_id="op-alpha")

    assert first.status_code == 200
    assert second.status_code == 200
    with App.app.app_context():
        rows = App.SyncOp.query.filter_by(user_id=user_id, op_id="op-alpha").all()
        assert len(rows) == 1


def test_a_replay_is_labelled_as_one(client, user_id):
    """The client needs to distinguish "applied now" from "already applied"
    so it can settle a pending item without double-counting it locally."""
    login(client, user_id)
    first = dismiss(client, "Essay draft", op_id="op-beta")
    second = dismiss(client, "Essay draft", op_id="op-beta")

    assert REPLAY_HEADER not in first.headers
    assert second.headers.get(REPLAY_HEADER) == "1"


def test_a_replay_returns_the_original_response_verbatim(client, user_id):
    login(client, user_id)
    first = dismiss(client, "Problem set", op_id="op-gamma")
    second = dismiss(client, "Problem set", op_id="op-gamma")
    assert second.get_data(as_text=True) == first.get_data(as_text=True)
    assert second.status_code == first.status_code


def test_different_op_ids_are_different_writes(client, user_id):
    login(client, user_id)
    dismiss(client, "Reading one", op_id="op-one")
    dismiss(client, "Reading two", op_id="op-two")
    with App.app.app_context():
        assert App.SyncOp.query.filter_by(user_id=user_id).count() == 2


def test_a_request_without_an_op_id_is_not_recorded(client, user_id):
    """The ledger is opt-in. Ordinary online traffic should not grow it."""
    login(client, user_id)
    assert dismiss(client, "Untracked").status_code == 200
    with App.app.app_context():
        assert App.SyncOp.query.filter_by(user_id=user_id).count() == 0


# ── What must not be frozen into the ledger ─────────────────────────


def test_a_client_error_is_recorded_so_it_stays_a_client_error(client, user_id):
    """A missing title is a stable outcome. Replaying it must not suddenly
    succeed against different server state."""
    login(client, user_id)
    first = client.post("/dismiss", json={}, headers={OP_ID_HEADER: "op-bad"})
    second = client.post("/dismiss", json={}, headers={OP_ID_HEADER: "op-bad"})
    assert first.status_code == 400
    assert second.status_code == 400
    assert second.headers.get(REPLAY_HEADER) == "1"


def test_a_get_is_never_recorded(client, user_id):
    login(client, user_id)
    client.get("/tasks/unified", headers={OP_ID_HEADER: "op-get"})
    with App.app.app_context():
        assert App.SyncOp.query.filter_by(op_id="op-get").count() == 0


def test_an_unusable_op_id_is_ignored_rather_than_stored(client, user_id):
    """Over-long or blank ids come from a broken client, not a student.
    Drop the key and handle the request normally.

    Newlines are not covered here because Werkzeug refuses to put one in a
    header at all — the transport rejects it before our check sees it. The
    sanitiser still screens for control characters, tested directly below,
    because the header parser is not the only thing we want to rely on.
    """
    login(client, user_id)
    for bad in ("x" * 65, " "):
        resp = client.post("/dismiss", json={"title": "Odd key"},
                           headers={OP_ID_HEADER: bad})
        assert resp.status_code == 200
        assert REPLAY_HEADER not in resp.headers
    with App.app.app_context():
        assert App.SyncOp.query.filter_by(user_id=user_id).count() == 0


@pytest.mark.parametrize("raw", [None, "", "   ", "x" * 65, "has\nnewline",
                                 "tab\there", "nul\x00byte"])
def test_the_op_id_sanitiser_rejects_unusable_keys(raw):
    from intelliplan.sync.idempotency import _clean_op_id
    assert _clean_op_id(raw) is None


@pytest.mark.parametrize("raw", ["op-1", " op-1 ", "a" * 64,
                                 "3f9c1b7e-2d40-4a11-8f2c-9b6e1d0a7c55"])
def test_the_op_id_sanitiser_accepts_ordinary_keys(raw):
    from intelliplan.sync.idempotency import _clean_op_id
    assert _clean_op_id(raw) == raw.strip()


# ── Scoping ─────────────────────────────────────────────────────────


def test_one_students_op_id_cannot_satisfy_anothers(client, user_id):
    """Op ids are client-generated, so two students can pick the same one.
    A cross-user hit would leak one student's response body to the other."""
    other_email = "sync+other@example.com"
    with App.app.app_context():
        User.query.filter_by(email=other_email).delete(synchronize_session=False)
        db.session.commit()
        other = User(email=other_email,
                     password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                     name="Other Student")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    try:
        login(client, user_id)
        dismiss(client, "Mine", op_id="collision")
        login(client, other_id)
        resp = dismiss(client, "Theirs", op_id="collision")
        assert REPLAY_HEADER not in resp.headers
        with App.app.app_context():
            assert App.SyncOp.query.filter_by(op_id="collision").count() == 2
    finally:
        with App.app.app_context():
            App.SyncOp.query.filter_by(user_id=other_id).delete(synchronize_session=False)
            DismissedAssignment.query.filter_by(user_id=other_id).delete(synchronize_session=False)
            User.query.filter_by(id=other_id).delete(synchronize_session=False)
            db.session.commit()


# ── The "did my ops land?" endpoint ─────────────────────────────────


def test_ops_check_reports_which_ops_already_applied(client, user_id):
    login(client, user_id)
    dismiss(client, "Applied one", op_id="op-known")
    resp = client.post("/api/sync/ops/check",
                       json={"op_ids": ["op-known", "op-unknown"]})
    body = resp.get_json()
    assert resp.status_code == 200
    assert "op-known" in body["applied"]
    assert "op-unknown" not in body["applied"]
    assert body["applied"]["op-known"]["path"] == "/dismiss"


def test_ops_check_rejects_a_non_list(client, user_id):
    login(client, user_id)
    assert client.post("/api/sync/ops/check", json={"op_ids": "nope"}).status_code == 400


def test_ops_check_of_an_empty_queue_is_not_an_error(client, user_id):
    login(client, user_id)
    resp = client.post("/api/sync/ops/check", json={"op_ids": []})
    assert resp.status_code == 200
    assert resp.get_json()["applied"] == {}


def test_ops_check_does_not_reveal_another_students_ops(client, user_id):
    login(client, user_id)
    dismiss(client, "Private", op_id="op-private")
    with client.session_transaction() as sess:
        sess.clear()
    body = client.post("/api/sync/ops/check",
                       json={"op_ids": ["op-private"]}).get_json()
    assert body["applied"] == {}
