"""Who may read and write a study group's chat.

The chat endpoints were the only room-scoped surface in the app with no
membership gate. Every other /api/groups/<id>/... endpoint answers 403 "not a
member"; these answered 200 with the transcript. The helper feeding them is
called ``_session_msg_owner_filter`` but scopes to the room and nothing else,
which is what made the gap easy to miss.

Concretely, before this: a signed-out visitor could read any study group's
chat -- private groups included -- by counting upwards through group ids, any
account could post into a group it had never joined, and any account could
copy the body of any message in the app into its own notes by walking message
ids.

Live sessions are deliberately not gated the same way: /live/<id> is a
public-by-link page whose invite URL is that same integer, so the id is the
share token rather than a secret. Those tests pin that it stays open.
"""

import pytest

import App
from App import (
    CourseNote, LiveSession, SessionMessage, StudyGroup, StudyGroupMember,
    User, db,
)


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            SessionMessage.query.delete()
            StudyGroupMember.query.delete()
            StudyGroup.query.delete()
            LiveSession.query.delete()
            User.query.filter(User.email.like("chat+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            SessionMessage.query.delete()
            StudyGroupMember.query.delete()
            StudyGroup.query.delete()
            LiveSession.query.delete()
            User.query.filter(User.email.like("chat+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


def make_user(email):
    with App.app.app_context():
        u = User(
            email=email,
            password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
            name=email.split("@")[0],
        )
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def logout(client):
    with client.session_transaction() as sess:
        sess.clear()


def make_group(member_ids=(), visibility="private"):
    with App.app.app_context():
        g = StudyGroup(name="Calc study", visibility=visibility)
        db.session.add(g)
        db.session.commit()
        for uid in member_ids:
            db.session.add(StudyGroupMember(group_id=g.id, user_id=uid))
        db.session.commit()
        return g.id


def post_message(group_id, author="Ada", body="the exam covers chapter 7"):
    with App.app.app_context():
        m = SessionMessage(
            context_type="group", context_id=group_id,
            user_id=None, author_name=author, body=body,
        )
        db.session.add(m)
        db.session.commit()
        return m.id


def messages_url(group_id):
    return f"/api/sessions/group/{group_id}/messages"


# ── Reading ──────────────────────────────────────────────────────────


def test_a_signed_out_visitor_cannot_read_a_groups_chat(client):
    gid = make_group()
    post_message(gid)
    logout(client)
    assert client.get(messages_url(gid)).status_code == 401


def test_a_non_member_cannot_read_a_groups_chat(client):
    gid = make_group(member_ids=[make_user("chat+member@example.com")])
    post_message(gid)
    login(client, make_user("chat+outsider@example.com"))
    resp = client.get(messages_url(gid))
    assert resp.status_code == 403
    assert b"the exam covers chapter 7" not in resp.data


def test_a_member_can_read_their_groups_chat(client):
    uid = make_user("chat+member@example.com")
    gid = make_group(member_ids=[uid])
    post_message(gid)
    login(client, uid)
    body = client.get(messages_url(gid)).get_json()
    assert body["status"] == "ok"
    assert [m["body"] for m in body["messages"]] == ["the exam covers chapter 7"]


def test_a_public_group_is_still_members_only(client):
    """"Public" governs who may find and join the group, not who may read
    what was said inside it."""
    gid = make_group(member_ids=[make_user("chat+in@example.com")],
                     visibility="public")
    post_message(gid)
    login(client, make_user("chat+out@example.com"))
    assert client.get(messages_url(gid)).status_code == 403


def test_a_missing_group_is_not_an_empty_room(client):
    login(client, make_user("chat+someone@example.com"))
    assert client.get(messages_url(999_999)).status_code == 404


# ── Posting ──────────────────────────────────────────────────────────


def test_a_non_member_cannot_post_into_a_group(client):
    gid = make_group(member_ids=[make_user("chat+member@example.com")])
    login(client, make_user("chat+outsider@example.com"))
    assert client.post(messages_url(gid), json={"body": "hello"}).status_code == 403
    with App.app.app_context():
        assert SessionMessage.query.filter_by(context_id=gid).count() == 0


def test_a_member_can_post_into_their_group(client):
    uid = make_user("chat+member@example.com")
    gid = make_group(member_ids=[uid])
    login(client, uid)
    assert client.post(messages_url(gid), json={"body": "hello"}).get_json()["status"] == "ok"
    with App.app.app_context():
        assert SessionMessage.query.filter_by(context_id=gid).count() == 1


# ── Saving a message to notes ────────────────────────────────────────


def test_a_non_member_cannot_copy_a_message_into_their_notes(client):
    gid = make_group(member_ids=[make_user("chat+member@example.com")])
    mid = post_message(gid)
    outsider = make_user("chat+outsider@example.com")
    login(client, outsider)

    assert client.post(f"/api/sessions/messages/{mid}/save").status_code == 403
    with App.app.app_context():
        assert CourseNote.query.filter_by(user_id=outsider).count() == 0
        # The shared flag on someone else's message must not have moved either.
        assert SessionMessage.query.get(mid).saved_to_library is not True


def test_a_member_can_save_a_message_from_their_own_group(client):
    uid = make_user("chat+member@example.com")
    gid = make_group(member_ids=[uid])
    mid = post_message(gid)
    login(client, uid)
    assert client.post(f"/api/sessions/messages/{mid}/save").get_json()["status"] == "ok"
    with App.app.app_context():
        assert CourseNote.query.filter_by(user_id=uid).count() == 1


# ── Live sessions stay open by link ──────────────────────────────────


def make_live_session():
    with App.app.app_context():
        s = LiveSession(title="Open room", room_slug="slug-for-tests")
        db.session.add(s)
        db.session.commit()
        return s.id


def test_a_live_room_is_readable_by_anyone_with_the_link(client):
    sid = make_live_session()
    logout(client)
    resp = client.get(f"/api/sessions/live/{sid}/messages")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_a_live_room_that_does_not_exist_is_a_404(client):
    logout(client)
    assert client.get("/api/sessions/live/999999/messages").status_code == 404
