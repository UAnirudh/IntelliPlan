"""Study Buddies: invites, privacy, the under-13 gate, nudges, friend streaks.

The feature shows one student's daily activity to another account, so most
of what is tested here is what it must NOT do:

* never expose grades, assignments, courses or email addresses;
* never work for an under-13 account, an account with no known age, or one
  still waiting on its parent's consent;
* never share anything until *both* students have said yes;
* never let a nudge button become a spam cannon.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date, timedelta

import pytest

import App
from App import (ActiveSession, BuddyNudge, ImportedGrade, ManualTask, NotificationOutbox,
                 StudyBuddy, User, UserStreak, bcrypt, db)
from intelliplan.services import buddies as rules
from time_utils import utcnow

PREFIX = "buddy+"
THIS_YEAR = utcnow().year


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like(PREFIX + "%")).all()]
    if ids:
        BuddyNudge.query.filter(
            BuddyNudge.sender_id.in_(ids) | BuddyNudge.recipient_id.in_(ids)
        ).delete(synchronize_session=False)
        StudyBuddy.query.filter(
            StudyBuddy.user_low_id.in_(ids) | StudyBuddy.user_high_id.in_(ids)
        ).delete(synchronize_session=False)
        for model in (UserStreak, ActiveSession, ImportedGrade, ManualTask, NotificationOutbox):
            model.query.filter(model.user_id.in_(ids)).delete(synchronize_session=False)
        User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()


def _user(tag, *, age=16, opted_in=False, name=None, **extra):
    with App.app.app_context():
        u = User(
            email=f"{PREFIX}{tag}@example.com",
            name=name or tag.title(),
            password_hash=bcrypt.generate_password_hash("pw-12345678").decode(),
            birth_year=(THIS_YEAR - age) if age is not None else None,
            parent_consent_granted=True,
            buddies_opt_in=opted_in,
            **extra,
        )
        db.session.add(u)
        db.session.commit()
        db.session.add(UserStreak(user_id=u.id, timezone="UTC"))
        db.session.commit()
        return u.id


def _as(client, uid):
    with client.session_transaction() as sess:
        sess.clear()
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


def _code(uid):
    with App.app.app_context():
        return App._ensure_referral_code(db.session.get(User, uid))


def _pair(client, a, b):
    """a invites, b requests, a confirms. Returns the link id."""
    _as(client, b)
    r = client.post("/api/buddies/request", json={"code": _code(a)})
    assert r.status_code == 200, r.get_json()
    _as(client, a)
    link_id = client.get("/api/buddies").get_json()["incoming"][0]["id"]
    assert client.post(f"/api/buddies/{link_id}/confirm").status_code == 200
    return link_id


def _earlier_today():
    """A moment earlier today (UTC), even just after midnight."""
    now = utcnow()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return max(midnight, now - timedelta(minutes=40))


def _studied(uid, days_ago_list):
    today = utcnow().date()
    with App.app.app_context():
        row = UserStreak.query.filter_by(user_id=uid).first()
        row.qualified_dates_json = json.dumps(
            sorted((today - timedelta(days=d)).isoformat() for d in days_ago_list))
        db.session.commit()


# ── eligibility (pure) ────────────────────────────────────────────────


class _U:
    def __init__(self, **kw):
        self.role = "student"
        self.parent_email = None
        self.parent_consent_granted = True
        self.birth_year = None
        self.__dict__.update(kw)


@pytest.mark.parametrize("user,reason", [
    (_U(birth_year=THIS_YEAR - 10), "under_13"),
    # The youngest a birth year allows: born 13 years ago may still be 12.
    (_U(birth_year=THIS_YEAR - 13), "under_13"),
    (_U(birth_year=None), "unknown_age"),
    (_U(birth_year=THIS_YEAR - 16, parent_email="p@example.com", parent_consent_granted=False),
     "awaiting_parent_consent"),
    (_U(birth_year=THIS_YEAR - 40, role="parent"), "students_only"),
])
def test_who_cannot_use_buddies(user, reason):
    assert rules.eligibility(user, utcnow()) == (False, reason)


def test_a_teen_can():
    assert rules.eligibility(_U(birth_year=THIS_YEAR - 15), utcnow()) == (True, "ok")


# ── friend streak math (pure) ─────────────────────────────────────────


D = date(2026, 5, 10)


def _days(*offsets):
    return {D - timedelta(days=o) for o in offsets}


def test_streak_counts_consecutive_joint_days():
    s = rules.advance_pair_streak(count=0, longest=0, last_joint=None,
                                  days_a=_days(0, 1, 2, 5), days_b=_days(0, 1, 2, 3), today=D)
    assert (s.count, s.both_today, s.alive, s.at_risk, s.shown) == (3, True, True, False, 3)


def test_one_side_alone_does_not_count():
    s = rules.advance_pair_streak(count=0, longest=0, last_joint=None,
                                  days_a=_days(0, 1, 2), days_b=set(), today=D)
    assert s.count == 0 and s.shown == 0


def test_streak_ending_yesterday_is_alive_but_at_risk():
    s = rules.advance_pair_streak(count=0, longest=0, last_joint=None,
                                  days_a=_days(1, 2), days_b=_days(0, 1, 2), today=D)
    assert (s.count, s.alive, s.at_risk, s.shown) == (2, True, True, 2)


def test_a_missed_day_breaks_it_and_the_next_joint_day_restarts_at_one():
    s = rules.advance_pair_streak(count=4, longest=4, last_joint=D - timedelta(days=3),
                                  days_a=_days(0), days_b=_days(0), today=D)
    assert (s.count, s.longest) == (1, 4)
    broken = rules.advance_pair_streak(count=4, longest=4, last_joint=D - timedelta(days=2),
                                       days_a=set(), days_b=set(), today=D)
    assert broken.shown == 0 and broken.alive is False and broken.longest == 4


def test_folding_is_idempotent_and_extends_a_stored_run():
    first = rules.advance_pair_streak(count=10, longest=12, last_joint=D - timedelta(days=1),
                                      days_a=_days(0, 1), days_b=_days(0, 1), today=D)
    assert first.count == 11
    again = rules.advance_pair_streak(count=first.count, longest=first.longest,
                                      last_joint=first.last_joint,
                                      days_a=_days(0, 1), days_b=_days(0, 1), today=D)
    assert again.count == 11


def test_days_after_the_pairs_today_wait():
    """A buddy whose local date is already tomorrow counts once the pair's
    day (the earlier of the two) gets there."""
    s = rules.advance_pair_streak(count=0, longest=0, last_joint=None,
                                  days_a={D + timedelta(days=1)}, days_b={D + timedelta(days=1)}, today=D)
    assert s.count == 0


# ── invite / request / confirm / remove ───────────────────────────────


def test_off_by_default(client):
    a = _user("a")
    _as(client, a)
    body = client.get("/api/buddies").get_json()
    assert body["eligible"] is True and body["enabled"] is False
    assert "invite_url" not in body


def test_invite_link_reuses_the_referral_code(client):
    a = _user("a", opted_in=True)
    _as(client, a)
    url = client.get("/api/buddies").get_json()["invite_url"]
    assert url.endswith("/buddies/join/" + _code(a))


def test_the_join_link_remembers_the_invite_and_attributes_the_referral(client):
    a = _user("a", opted_in=True)
    r = client.get(f"/buddies/join/{_code(a)}")
    assert r.status_code == 302 and "/register" in r.headers["Location"]
    with client.session_transaction() as sess:
        assert sess["pending_buddy_code"] == _code(a)
        assert sess["pending_referral"] == a


def test_a_request_is_not_a_buddy_until_confirmed(client):
    a = _user("a", opted_in=True)
    b = _user("b")
    _as(client, b)
    body = client.post("/api/buddies/request", json={"code": _code(a)}).get_json()
    assert body["state"] == "pending"
    assert body["enabled"] is True  # sending the request is b's opt-in
    assert body["buddies"] == [] and len(body["outgoing"]) == 1
    _as(client, a)
    body = client.get("/api/buddies").get_json()
    assert body["buddies"] == [] and body["incoming"][0]["name"] == "B"
    # The requester cannot confirm their own request.
    _as(client, b)
    assert client.post(f"/api/buddies/{body['incoming'][0]['id']}/confirm").status_code == 404


def test_confirmed_buddies_see_each_other_and_either_can_remove(client):
    a = _user("a", opted_in=True)
    b = _user("b")
    link = _pair(client, a, b)
    _as(client, b)
    assert [x["name"] for x in client.get("/api/buddies").get_json()["buddies"]] == ["A"]
    assert client.post(f"/api/buddies/{link}/remove").status_code == 200
    _as(client, a)
    assert client.get("/api/buddies").get_json()["buddies"] == []


def test_you_cannot_buddy_yourself(client):
    a = _user("a", opted_in=True)
    _as(client, a)
    assert client.post("/api/buddies/request", json={"code": _code(a)}).status_code == 404


def test_at_most_five_buddies(client):
    a = _user("a", opted_in=True)
    for i in range(rules.MAX_BUDDIES):
        _pair(client, a, _user(f"f{i}"))
    late = _user("late")
    _as(client, late)
    assert client.post("/api/buddies/request", json={"code": _code(a)}).status_code == 404


def test_a_stranger_cannot_act_on_someone_elses_link(client):
    a = _user("a", opted_in=True)
    b = _user("b")
    link = _pair(client, a, b)
    c = _user("c", opted_in=True)
    _as(client, c)
    for action in ("remove", "block", "nudge", "confirm"):
        assert client.post(f"/api/buddies/{link}/{action}").status_code == 404


def test_block_stops_new_requests_and_only_the_blocker_can_lift_it(client):
    a = _user("a", opted_in=True)
    b = _user("b", opted_in=True)
    link = _pair(client, a, b)
    _as(client, a)
    assert client.post(f"/api/buddies/{link}/block").status_code == 200
    _as(client, b)
    body = client.get("/api/buddies").get_json()
    assert body["buddies"] == [] and body["blocked"] == []  # b sees nothing at all
    assert client.post("/api/buddies/request", json={"code": _code(a)}).status_code == 404
    assert client.post(f"/api/buddies/{link}/remove").status_code == 404
    _as(client, a)
    assert client.post(f"/api/buddies/{link}/remove").status_code == 200  # unblock


# ── privacy ───────────────────────────────────────────────────────────


def test_a_buddy_card_carries_activity_and_nothing_else(client):
    a = _user("a", opted_in=True)
    b = _user("b", name="Maya Rodriguez")
    with App.app.app_context():
        db.session.add(ImportedGrade(user_id=b, course="AP Chemistry", percentage=61.5, letter="D-"))
        db.session.add(ManualTask(user_id=b, title="Secret essay on failing chem", course="AP Chemistry"))
        db.session.add(ActiveSession(user_id=b, title="Chem retake prep", course="AP Chemistry",
                                     planned_minutes=30, active_seconds=25 * 60, state="completed",
                                     started_at=_earlier_today(), updated_at=utcnow()))
        db.session.commit()
    _pair(client, a, b)
    _as(client, a)
    r = client.get("/api/buddies")
    card = r.get_json()["buddies"][0]
    assert set(card) == {"id", "name", "sharing", "nudged_you_today", "studied_today",
                         "focus_minutes_today", "streak", "streak_longest",
                         "streak_both_today", "streak_at_risk", "can_nudge"}
    assert card["name"] == "Maya"  # first name only
    assert card["studied_today"] is True and card["focus_minutes_today"] == 25
    text = r.get_data(as_text=True)
    for secret in ("Chemistry", "61.5", "D-", "Secret essay", "Chem retake", "Rodriguez",
                   f"{PREFIX}b@example.com"):
        assert secret not in text


def test_turning_it_off_pauses_sharing_immediately(client):
    a = _user("a", opted_in=True)
    b = _user("b")
    _pair(client, a, b)
    _studied(b, [0])
    _as(client, b)
    client.post("/api/buddies/settings", json={"enabled": False})
    _as(client, a)
    card = client.get("/api/buddies").get_json()["buddies"][0]
    assert card["sharing"] is False
    assert card["studied_today"] is None and card["focus_minutes_today"] is None
    assert card["can_nudge"] is False


# ── under-13 / consent ────────────────────────────────────────────────


def test_an_under_13_cannot_turn_it_on_or_join(client):
    a = _user("a", opted_in=True)
    kid = _user("kid", age=11)
    _as(client, kid)
    body = client.get("/api/buddies").get_json()
    assert body["eligible"] is False and body["reason"] == "under_13"
    assert client.post("/api/buddies/settings", json={"enabled": True}).status_code == 403
    assert client.post("/api/buddies/request", json={"code": _code(a)}).status_code == 403
    with App.app.app_context():
        assert db.session.get(User, kid).buddies_opt_in is False


def test_an_under_13_invite_link_reaches_nobody(client):
    """Even a pre-existing opt-in flag does not make a child reachable, and
    the refusal does not say why."""
    kid = _user("kid", age=11, opted_in=True)
    teen = _user("teen")
    _as(client, teen)
    r = client.post("/api/buddies/request", json={"code": _code(kid)})
    assert r.status_code == 404
    assert "13" not in r.get_json()["message"]


def test_an_account_waiting_on_parental_consent_is_blocked(client):
    waiting = _user("waiting", age=16, parent_email="parent@example.com")
    with App.app.app_context():
        db.session.get(User, waiting).parent_consent_granted = False
        db.session.commit()
    _as(client, waiting)
    assert client.post("/api/buddies/settings", json={"enabled": True}).status_code == 403


def test_unknown_age_is_treated_as_a_child(client):
    who = _user("who", age=None)
    _as(client, who)
    body = client.get("/api/buddies").get_json()
    assert body["reason"] == "unknown_age" and body["enabled"] is False


def test_a_buddy_who_becomes_ineligible_stops_sharing(client):
    a = _user("a", opted_in=True)
    b = _user("b")
    _pair(client, a, b)
    with App.app.app_context():
        db.session.get(User, b).birth_year = THIS_YEAR - 11  # corrected age
        db.session.commit()
    _as(client, a)
    assert client.get("/api/buddies").get_json()["buddies"][0]["sharing"] is False


# ── nudges ────────────────────────────────────────────────────────────


def test_nudge_is_once_per_buddy_per_day_and_goes_through_the_outbox(client):
    a = _user("a", opted_in=True)
    b = _user("b", email_reminders_opt_in=True)
    link = _pair(client, a, b)
    _as(client, a)
    first = client.post(f"/api/buddies/{link}/nudge")
    assert first.status_code == 200
    second = client.post(f"/api/buddies/{link}/nudge")
    assert second.status_code == 429
    with App.app.app_context():
        rows = NotificationOutbox.query.filter_by(user_id=b, kind="buddy_nudge").all()
        assert len(rows) == 1
        assert rows[0].title == "A nudged you"
    _as(client, b)
    assert client.get("/api/buddies").get_json()["buddies"][0]["nudged_you_today"] is True


def test_nudges_are_capped_per_day_across_buddies(client):
    a = _user("a", opted_in=True)
    links = [_pair(client, a, _user(f"n{i}")) for i in range(rules.NUDGES_PER_DAY + 1)]
    _as(client, a)
    codes = [client.post(f"/api/buddies/{link}/nudge").status_code for link in links]
    assert codes == [200] * rules.NUDGES_PER_DAY + [429]


def test_no_nudge_for_a_buddy_who_already_studied(client):
    a = _user("a", opted_in=True)
    b = _user("b")
    _pair(client, a, b)
    _studied(b, [0])
    _as(client, a)
    assert client.get("/api/buddies").get_json()["buddies"][0]["can_nudge"] is False


# ── the shared streak, end to end ─────────────────────────────────────


def test_the_friend_streak_is_stored_and_shared(client):
    a = _user("a", opted_in=True)
    b = _user("b")
    link = _pair(client, a, b)
    _studied(a, [0, 1, 2, 3])
    _studied(b, [0, 1, 2])
    _as(client, a)
    card = client.get("/api/buddies").get_json()["buddies"][0]
    assert card["streak"] == 3 and card["streak_both_today"] is True
    _as(client, b)
    assert client.get("/api/buddies").get_json()["buddies"][0]["streak"] == 3
    with App.app.app_context():
        row = db.session.get(StudyBuddy, link)
        assert row.streak_count == 3
        assert row.streak_last_date == utcnow().date().isoformat()


# ── pages ─────────────────────────────────────────────────────────────


def test_the_pages_render_with_the_card(client):
    a = _user("a", opted_in=True)
    _as(client, a)
    for path in ("/buddies", "/settings"):
        r = client.get(path)
        assert r.status_code == 200, path
    # The dashboard sends a fresh account to onboarding first, so check its
    # template carries the card rather than following that redirect.
    dashboard = (pathlib.Path(App.__file__).parent / "Main_Project" / "templates"
                 / "dashboard.html").read_text(encoding="utf-8")
    assert '{% include "partials/buddies_card.html" %}' in dashboard
    assert 'id="budCard"' in client.get("/buddies").get_data(as_text=True)
    assert 'id="focusShieldCard"' in client.get("/settings").get_data(as_text=True)
    assert "noindex" in client.get("/buddies").headers.get("X-Robots-Tag", "noindex")


def test_buddies_page_needs_sign_in(client):
    r = client.get("/buddies")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
