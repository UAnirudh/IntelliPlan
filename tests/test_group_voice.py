"""Voice-room presence inside study groups.

The interesting behaviour is not join and leave — it is what happens when
leave never arrives, which is the normal case: a closed laptop, a dropped
connection, a swiped-away tab. These tests pin that the roster is derived
from the last heartbeat rather than trusted from a leave call.
"""

from datetime import datetime, timedelta

import pytest

import App
from App import StudyGroup, StudyGroupMember, User, VoiceSeat, db
from intelliplan.api import group_voice


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    VoiceSeat.query.delete()
    StudyGroupMember.query.delete()
    StudyGroup.query.filter(StudyGroup.name.like("voicetest%")).delete(
        synchronize_session=False)
    User.query.filter(User.email.like("voice+%")).delete(synchronize_session=False)
    db.session.commit()


def make_user(email):
    with App.app.app_context():
        u = User(email=email,
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                 name=email.split("+")[1].split("@")[0].title())
        db.session.add(u)
        db.session.commit()
        return u.id


def make_group(owner_id, members=()):
    with App.app.app_context():
        g = StudyGroup(name="voicetest group", topic="AP Calc BC", owner_id=owner_id)
        db.session.add(g)
        db.session.commit()
        for uid in (owner_id,) + tuple(members):
            db.session.add(StudyGroupMember(group_id=g.id, user_id=uid))
        db.session.commit()
        return g.id


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def age_seat(user_id, group_id, seconds):
    """Backdate a seat's last heartbeat, standing in for a client that
    went away without saying so."""
    with App.app.app_context():
        seat = VoiceSeat.query.filter_by(group_id=group_id, user_id=user_id).first()
        seat.last_seen_at = datetime.utcnow() - timedelta(seconds=seconds)
        db.session.commit()


# ── membership is the gate ────────────────────────────────────────


def test_a_non_member_cannot_see_the_roster(client):
    owner = make_user("voice+owner@example.com")
    outsider = make_user("voice+outsider@example.com")
    gid = make_group(owner)
    login(client, outsider)
    assert client.get(f"/api/groups/{gid}/voice").status_code == 403


def test_a_non_member_cannot_join(client):
    owner = make_user("voice+owner2@example.com")
    outsider = make_user("voice+outsider2@example.com")
    gid = make_group(owner)
    login(client, outsider)
    assert client.post(f"/api/groups/{gid}/voice/join").status_code == 403


# ── the room name is not guessable, and not handed out early ──────


def test_the_room_url_is_withheld_until_you_are_seated(client):
    """Knowing a meet.jit.si room name is enough to walk into it, so the
    roster must not leak it to someone who has not joined."""
    owner = make_user("voice+url@example.com")
    gid = make_group(owner)
    login(client, owner)

    body = client.get(f"/api/groups/{gid}/voice").get_json()
    assert body["joined"] is False
    assert body["room_url"] is None
    assert body["embed_url"] is None

    joined = client.post(f"/api/groups/{gid}/voice/join").get_json()
    assert joined["room_url"].startswith("https://meet.jit.si/intelliplan-")


def test_the_room_name_is_long_enough_to_not_be_guessed(client):
    owner = make_user("voice+slug@example.com")
    gid = make_group(owner)
    login(client, owner)
    url = client.post(f"/api/groups/{gid}/voice/join").get_json()["room_url"]
    slug = url.rsplit("intelliplan-", 1)[1]
    assert len(slug) >= 12
    assert str(gid) != slug


def test_the_embed_disables_video_not_just_mutes_it(client):
    owner = make_user("voice+embed@example.com")
    gid = make_group(owner)
    login(client, owner)
    embed = client.post(f"/api/groups/{gid}/voice/join").get_json()["embed_url"]
    assert "startWithVideoMuted=true" in embed
    assert "startAudioOnly=true" in embed
    assert "startWithAudioMuted=true" in embed


# ── presence ──────────────────────────────────────────────────────


def test_joining_seats_you_muted(client):
    owner = make_user("voice+mute@example.com")
    gid = make_group(owner)
    login(client, owner)
    body = client.post(f"/api/groups/{gid}/voice/join").get_json()
    assert body["count"] == 1
    assert body["seats"][0]["muted"] is True
    assert body["seats"][0]["is_you"] is True


def test_joining_twice_does_not_seat_you_twice(client):
    """Two tabs, or a double-click. Either way there is one of you."""
    owner = make_user("voice+double@example.com")
    gid = make_group(owner)
    login(client, owner)
    client.post(f"/api/groups/{gid}/voice/join")
    body = client.post(f"/api/groups/{gid}/voice/join").get_json()
    assert body["count"] == 1
    with App.app.app_context():
        assert VoiceSeat.query.filter_by(group_id=gid, user_id=owner).count() == 1


def test_two_members_see_each_other(client):
    a = make_user("voice+a@example.com")
    b = make_user("voice+b@example.com")
    gid = make_group(a, members=(b,))

    login(client, a)
    client.post(f"/api/groups/{gid}/voice/join")
    login(client, b)
    body = client.post(f"/api/groups/{gid}/voice/join").get_json()

    assert body["count"] == 2
    assert {s["is_you"] for s in body["seats"]} == {True, False}


def test_leaving_frees_the_seat(client):
    owner = make_user("voice+leave@example.com")
    gid = make_group(owner)
    login(client, owner)
    client.post(f"/api/groups/{gid}/voice/join")
    body = client.post(f"/api/groups/{gid}/voice/leave").get_json()
    assert body["count"] == 0
    assert body["joined"] is False


# ── the part that matters: leave never arrives ────────────────────


def test_a_seat_that_stops_reporting_drops_out_of_the_roster(client):
    a = make_user("voice+ghost@example.com")
    b = make_user("voice+watcher@example.com")
    gid = make_group(a, members=(b,))

    login(client, a)
    client.post(f"/api/groups/{gid}/voice/join")
    login(client, b)
    assert client.post(f"/api/groups/{gid}/voice/join").get_json()["count"] == 2

    # A's laptop closes. No leave is sent, ever.
    age_seat(a, gid, group_voice.VOICE_STALE_SECONDS + 5)

    body = client.post(f"/api/groups/{gid}/voice/heartbeat").get_json()
    assert body["count"] == 1
    assert [s["is_you"] for s in body["seats"]] == [True]


def test_a_seat_just_inside_the_window_is_still_in_the_room(client):
    """The stale window has to tolerate a phone that backgrounded the tab
    and throttled its timers — dropping someone mid-sentence is worse
    than a roster that is a few seconds behind."""
    a = make_user("voice+slow@example.com")
    b = make_user("voice+slowwatch@example.com")
    gid = make_group(a, members=(b,))

    login(client, a)
    client.post(f"/api/groups/{gid}/voice/join")
    login(client, b)
    client.post(f"/api/groups/{gid}/voice/join")

    age_seat(a, gid, group_voice.VOICE_STALE_SECONDS - 10)
    assert client.post(f"/api/groups/{gid}/voice/heartbeat").get_json()["count"] == 2


def test_being_swept_out_is_reported_not_papered_over(client):
    """A heartbeat from someone who was already dropped must say so, so
    the UI shows the join button again instead of pretending."""
    owner = make_user("voice+swept@example.com")
    gid = make_group(owner)
    login(client, owner)
    client.post(f"/api/groups/{gid}/voice/join")

    with App.app.app_context():
        VoiceSeat.query.filter_by(group_id=gid, user_id=owner).delete()
        db.session.commit()

    body = client.post(f"/api/groups/{gid}/voice/heartbeat").get_json()
    assert body["joined"] is False


def test_rejoining_after_being_swept_reuses_the_row(client):
    """The stale row may still be there when the sweep has not run. A
    rejoin must not trip the one-seat-per-person constraint."""
    owner = make_user("voice+rejoin@example.com")
    gid = make_group(owner)
    login(client, owner)
    client.post(f"/api/groups/{gid}/voice/join")
    age_seat(owner, gid, group_voice.VOICE_STALE_SECONDS + 60)

    body = client.post(f"/api/groups/{gid}/voice/join").get_json()
    assert body["joined"] is True
    assert body["count"] == 1
    with App.app.app_context():
        assert VoiceSeat.query.filter_by(group_id=gid, user_id=owner).count() == 1


# ── mute state ────────────────────────────────────────────────────


def test_unmuting_is_visible_to_the_rest_of_the_room(client):
    a = make_user("voice+talker@example.com")
    b = make_user("voice+listener@example.com")
    gid = make_group(a, members=(b,))

    login(client, a)
    client.post(f"/api/groups/{gid}/voice/join")
    client.post(f"/api/groups/{gid}/voice/mute", json={"muted": False})

    login(client, b)
    body = client.post(f"/api/groups/{gid}/voice/join").get_json()
    other = [s for s in body["seats"] if not s["is_you"]][0]
    assert other["muted"] is False


def test_mute_state_rides_along_on_the_heartbeat(client):
    """One request per beat, not two — this runs every twelve seconds per
    person in the call."""
    owner = make_user("voice+beat@example.com")
    gid = make_group(owner)
    login(client, owner)
    client.post(f"/api/groups/{gid}/voice/join")

    body = client.post(f"/api/groups/{gid}/voice/heartbeat",
                       json={"muted": False}).get_json()
    assert body["seats"][0]["muted"] is False


def test_muting_without_a_seat_is_a_conflict_not_a_crash(client):
    owner = make_user("voice+nomute@example.com")
    gid = make_group(owner)
    login(client, owner)
    assert client.post(f"/api/groups/{gid}/voice/mute",
                       json={"muted": True}).status_code == 409


# ── capacity ──────────────────────────────────────────────────────


def test_the_room_has_a_ceiling(client, monkeypatch):
    a = make_user("voice+cap1@example.com")
    b = make_user("voice+cap2@example.com")
    gid = make_group(a, members=(b,))

    monkeypatch.setattr(group_voice, "MAX_SEATS", 1)
    login(client, a)
    assert client.post(f"/api/groups/{gid}/voice/join").status_code == 200
    login(client, b)
    r = client.post(f"/api/groups/{gid}/voice/join")
    assert r.status_code == 409
    assert "full" in r.get_json()["message"].lower()


def test_someone_already_seated_is_not_turned_away_by_a_full_room(client, monkeypatch):
    """A heartbeat-driven rejoin from someone who is already in the room
    must not be rejected for capacity — they are not taking a new seat."""
    a = make_user("voice+incap@example.com")
    gid = make_group(a)
    monkeypatch.setattr(group_voice, "MAX_SEATS", 1)
    login(client, a)
    client.post(f"/api/groups/{gid}/voice/join")
    assert client.post(f"/api/groups/{gid}/voice/join").status_code == 200
