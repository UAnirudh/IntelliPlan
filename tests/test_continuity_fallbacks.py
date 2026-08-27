"""Continuity when a dependency is down, and the camera self-view.

The scheduler already runs a deterministic planner *before* the AI, so the
common case is covered. This pins the layer under it: when that planner
abstains and the AI is also unreachable, a student still gets a plan built
from their deadlines and free time rather than "please try again", which for
someone sitting down at 9pm to plan their week means "no".

Also here: the self-view. The camera element is created detached so nothing
else on the page can read it, which also meant nobody could see what the
check-in was looking at. Attaching the *same* stream to a preview the student
controls makes "frames stay on this device" checkable rather than asserted.
"""

from datetime import date, timedelta

import pytest

import App
import fallback_scheduler
from App import User, bcrypt, db

HEADERS = {"Origin": "http://localhost"}


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
    User.query.filter(User.email.like("cont+%")).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture
def student(client):
    with App.app.app_context():
        user = User(email="cont+a@example.com",
                    password_hash=bcrypt.generate_password_hash("hunter2ok").decode())
        db.session.add(user)
        db.session.commit()
        uid = user.id
        identity = App._get_or_create_identity(uid)
        identity.completed = True
        db.session.commit()
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return uid


def task(title, course, due_in_days, minutes):
    """The shape the route hands to the planner."""
    due = (date.today() + timedelta(days=due_in_days)).isoformat()
    return {"title": title, "assignment": title, "course": course,
            "due_date": due, "estimated_minutes": minutes,
            "estimated_time": minutes, "priority": "high",
            "difficulty": "medium"}


@pytest.fixture
def ai_down(monkeypatch):
    def dead(*args, **kwargs):
        raise RuntimeError("all AI providers unavailable")
    monkeypatch.setattr(App, "ai_chat", dead)


@pytest.fixture
def planner_abstains(monkeypatch):
    """Force the pre-AI deterministic planner to decline, so the request
    reaches the AI path this fallback sits under."""
    monkeypatch.setattr(App, "_build_planner_schedule", lambda *a, **k: None)


def generate(client, tasks):
    return client.post("/generate_schedule", json={
        "assignments": tasks, "hours_per_day": 2,
        "preferred_time": "evening", "skip_clarify": True,
    }, headers=HEADERS)


# ── A plan still arrives with the AI down ────────────────────

def test_a_schedule_is_still_produced(client, student, ai_down, planner_abstains):
    body = generate(client, [task("Problem set", "Math", 1, 45),
                             task("Essay", "English", 3, 90)]).get_json()
    assert body["status"] == "ok"
    assert body["degraded"] is True
    assert body["data"]["schedule"]


def test_the_student_is_told_why_it_looks_different(client, student, ai_down,
                                                    planner_abstains):
    """The per-day tips and overview prose are missing. Saying so beats
    inventing flat substitutes, and beats leaving them wondering."""
    body = generate(client, [task("Problem set", "Math", 1, 45)]).get_json()
    assert "built" in body["degraded_message"].lower()


def test_the_work_is_actually_in_the_plan(client, student, ai_down, planner_abstains):
    body = generate(client, [task("Problem set", "Math", 1, 45),
                             task("Essay", "English", 3, 90)]).get_json()
    titles = {b.get("assignment")
              for day in body["data"]["schedule"]
              for b in day["blocks"] if not b.get("is_break")}
    assert "Problem set" in titles
    assert "Essay" in titles


def test_with_nothing_to_schedule_it_says_so(client, student, ai_down,
                                             planner_abstains):
    """An empty week returned as a success would be worse than the error."""
    response = generate(client, [])
    assert response.get_json()["status"] == "error"


# ── The planner itself ───────────────────────────────────────

def test_estimates_are_respected(client):
    """The allocator reads est_minutes/duration_minutes. Callers upstream
    spell it several other ways, and a mismatch does not error — it silently
    falls back to 30 minutes, so a two-hour essay is scheduled as half an
    hour and the plan looks plausible while being wrong."""
    plan = fallback_scheduler.build_schedule(
        [task("Essay", "English", 3, 90)], hours_per_day=3)
    total = sum(int(b.get("duration_minutes") or 0)
                for day in plan["schedule"]
                for b in day["blocks"] if not b.get("is_break"))
    assert total == 90


def test_the_most_urgent_work_comes_first(client):
    plan = fallback_scheduler.build_schedule([
        task("Later", "History", 6, 30),
        task("Tomorrow", "Math", 1, 30),
    ], hours_per_day=2)
    first_day = plan["schedule"][0]["blocks"]
    assert any(b.get("assignment") == "Tomorrow" for b in first_day)


def test_a_long_task_is_split_into_sittings(client):
    plan = fallback_scheduler.build_schedule(
        [task("Big project", "Science", 5, 180)], hours_per_day=2)
    blocks = [b for day in plan["schedule"] for b in day["blocks"]
              if not b.get("is_break")]
    assert len(blocks) > 1


def test_an_empty_list_yields_an_empty_plan_not_an_error(client):
    """This sits on an error path already; raising here would replace one
    failure with another."""
    assert fallback_scheduler.build_schedule([])["schedule"] == []


def test_it_is_marked_as_degraded(client):
    plan = fallback_scheduler.build_schedule([task("X", "Y", 2, 30)])
    assert plan["degraded"] is True
    assert plan["degraded_reason"] == "ai_unavailable"


def test_no_daily_tips_are_invented(client):
    """Writing about the specific week is what the AI was for. A generic
    substitute would read as advice while being none."""
    plan = fallback_scheduler.build_schedule([task("X", "Y", 2, 30)])
    assert all(day["daily_tip"] == "" for day in plan["schedule"])


def test_work_that_does_not_fit_is_reported_not_dropped(client):
    """Silently losing an assignment is the worst possible failure for a
    planner."""
    plan = fallback_scheduler.build_schedule(
        [task(f"Task {i}", "Course", 1, 120) for i in range(8)],
        hours_per_day=1, days=2)
    placed = sum(1 for day in plan["schedule"] for b in day["blocks"]
                 if not b.get("is_break"))
    assert placed or plan["unplaced"]
    if plan["unplaced"]:
        assert "did not fit" in plan["overview"]


# ── Everything else already fails soft ───────────────────────

def test_a_failed_audit_write_does_not_break_the_request(client, monkeypatch):
    monkeypatch.setattr(App.db.session, "add",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    App.log_security_event("test", email="cont+x@example.com")   # must not raise


def test_a_broken_csrf_guard_does_not_take_the_site_down(client, monkeypatch):
    monkeypatch.setattr(App.request_guards, "cross_site_violation",
                        lambda: (_ for _ in ()).throw(RuntimeError("guard broken")))
    assert client.get("/").status_code == 200


def test_a_policy_lookup_failure_returns_no_notice_rather_than_an_error(
        client, monkeypatch):
    monkeypatch.setattr(App.policy_versions, "describe",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    body = client.get("/api/policy/pending").get_json()
    assert body["status"] == "ok"
    assert body["pending"] == []


def test_an_unreachable_captcha_verifier_still_lets_people_in(client, monkeypatch):
    monkeypatch.setenv("RECAPTCHA_SITE_KEY", "s")
    monkeypatch.setenv("RECAPTCHA_SECRET_KEY", "k")
    monkeypatch.setattr(App.bot_protection.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    # check_recaptcha reads the submitted form, so it needs a request.
    with App.app.test_request_context("/login/account", method="POST",
                                      data={"g-recaptcha-response": "tok"}):
        assert App.check_recaptcha("login") is None


def test_a_missing_encryption_key_does_not_stop_tokens_being_stored(
        client, monkeypatch):
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    App.secret_box.reset_cache()
    assert App.secret_box.encrypt("value") == "value"
    App.secret_box.reset_cache()


# ── The self-view ────────────────────────────────────────────

FOCUS_JS = "static/js/ip-focus.js"
ACTIVE_JS = "static/js/ip-active.js"


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_the_page_offers_a_self_view(client, student):
    html = client.get("/active").data.decode("utf-8", "ignore")
    assert 'id="ipaSelfView"' in html
    assert "what the camera sees" in html


def test_it_shows_the_same_stream_the_detector_reads(client):
    """A second capture would prove nothing about the first. The point is
    that what they see *is* the input being examined."""
    source = read(FOCUS_JS)
    attach = source[source.find("CameraMonitor.prototype.attachPreview"):]
    attach = attach[:attach.find("CameraMonitor.prototype.detachPreview")]
    assert "element.srcObject = this.stream" in attach


def test_it_refuses_when_there_is_no_stream(client):
    """A preview button with no camera behind it is worse than none."""
    source = read(FOCUS_JS)
    attach = source[source.find("CameraMonitor.prototype.attachPreview"):]
    assert "if (!element || !this.stream) return false" in attach


def test_it_is_hidden_until_a_camera_is_actually_running(client):
    source = read(ACTIVE_JS)
    assert "selfWrap.hidden = !info.ok" in source


def test_stopping_the_camera_clears_the_reference(client):
    source = read(FOCUS_JS)
    assert "if (_activeCamera === this) _activeCamera = null" in source


def test_the_privacy_note_no_longer_claims_the_element_is_never_attached(client):
    """It said the video element is never attached to the document. A
    self-view attaches one, and leaving that sentence would make the privacy
    note false in exactly the way the Clarity paragraph was."""
    source = read(FOCUS_JS)
    assert "is never attached to the document" not in source
    assert "never added to the page by" in source
