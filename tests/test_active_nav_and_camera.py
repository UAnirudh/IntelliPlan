"""Active in the app nav, and the camera check-in actually starting.

Two reports. Active was reachable from the desktop sidebar but missing from
the phone tab bar entirely, so on a phone there was no way to navigate to it.

The camera check-in answered "This browser has no on-device face detection,
so the camera check-in is off" to essentially everyone: it required
``window.FaceDetector``, an API absent in Firefox and Safari and flagged off
in Chrome. The switch could be flipped and nothing behind it ever ran.
"""

import pytest

import App
from App import User, db, bcrypt

FOCUS_JS = "static/js/ip-focus.js"
ACTIVE_JS = "static/js/ip-active.js"


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            User.query.filter(User.email.like("nav+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            User.query.filter(User.email.like("nav+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


@pytest.fixture
def student(client):
    with App.app.app_context():
        user = User(email="nav+a@example.com",
                    password_hash=bcrypt.generate_password_hash("hunter2ok").decode())
        db.session.add(user)
        db.session.commit()
        uid = user.id
        # An unfinished profile bounces every app page to onboarding, which
        # would mean asserting against a redirect stub rather than the nav.
        identity = App._get_or_create_identity(uid)
        identity.completed = True
        db.session.commit()
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return uid


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# ── Reaching Active ──────────────────────────────────────────

def test_active_is_in_the_desktop_sidebar(client, student):
    html = client.get("/dashboard").data.decode("utf-8", "ignore")
    assert 'href="/active" data-nav-item="active"' in html


def test_active_is_in_the_phone_tab_bar(client, student):
    """It was in the sidebar but absent from this bar, which is the whole
    navigation on a phone."""
    html = client.get("/dashboard").data.decode("utf-8", "ignore")
    tabs = html[html.find('<div class="nav-group">'):]
    tabs = tabs[:tabs.find("</div>")]
    assert 'href="/active"' in tabs


def test_active_sits_next_to_scheduler(client, student):
    """Build the plan, then do the work — the nav should read in that order."""
    html = client.get("/dashboard").data.decode("utf-8", "ignore")
    assert html.find('href="/scheduler"') < html.find('href="/active"')


def test_the_active_page_keeps_its_slim_chrome(client, student):
    """Active deliberately renders without the app sidebar: the page exists
    to hold one task, and its blueprint is mounted standalone in
    tests/intelliplan/test_active_api.py where the sidebar's ``current_user``
    does not exist. Giving it the sidebar breaks that mount outright."""
    html = client.get("/active").data.decode("utf-8", "ignore")
    assert 'class="app-side"' not in html
    # Still not a dead end — the slim nav carries the app links.
    assert 'href="/scheduler"' in html


def test_the_active_page_still_renders_for_a_guest(client):
    assert client.get("/active").status_code == 200


# ── The camera check-in can actually start ───────────────────

def test_support_no_longer_requires_an_api_that_never_shipped():
    """The defect: FaceDetector is absent in Firefox and Safari and flagged
    off in Chrome, so this reported "unsupported" to almost everyone."""
    source = read(FOCUS_JS)
    supported = source[source.find("cameraSupported: function"):]
    supported = supported[:supported.find("},")]
    assert "FaceDetector" not in supported
    assert "getUserMedia" in supported


def test_a_motion_fallback_exists_for_browsers_without_face_detection():
    source = read(FOCUS_JS)
    assert "_sampleMotion" in source
    assert "'motion'" in source


def test_the_monitor_is_available_whenever_there_is_a_camera():
    """``available`` gated on FaceDetector, so start() refused before it ever
    asked for the camera."""
    source = read(FOCUS_JS)
    ctor = source[source.find("function CameraMonitor()"):]
    ctor = ctor[:ctor.find("CameraMonitor.prototype.start")]
    assert "this.available = !!(navigator.mediaDevices" in ctor


def test_the_reference_frame_is_cleared_on_stop():
    """Otherwise a resumed session compares its first frame against a scene
    from before the break and reads that as movement."""
    source = read(FOCUS_JS)
    stop = source[source.find("CameraMonitor.prototype.stop"):]
    stop = stop[:stop.find("/**", 10)]
    assert "this.prevFrame = null" in stop


def test_a_refused_camera_turns_the_switch_off():
    """It used to be set back to cameraSupported(), which left the toggle on
    with nothing behind it."""
    source = read(ACTIVE_JS)
    handler = source[source.find("onCameraStatus: function"):]
    handler = handler[:handler.find("\n      }")]
    assert "$('ipaFocusToggle').checked = false" in handler


# ── Saying which method is running ───────────────────────────

def test_the_page_has_somewhere_to_name_the_method(client, student):
    html = client.get("/active").data.decode("utf-8", "ignore")
    assert 'id="ipaFocusMethod"' in html


def test_the_status_message_names_the_method_rather_than_implying_one():
    """Telling someone "face detection" while it watches for movement is the
    same class of error as the policy that denied using session replay."""
    source = read(FOCUS_JS)
    assert "using face detection" in source
    assert "watching for movement" in source


def test_the_motion_copy_admits_what_it_cannot_do():
    """Movement can confirm somebody is there. It cannot show nobody is, and
    the UI has to say so rather than implying a capability."""
    source = read(ACTIVE_JS)
    assert "cannot tell that nobody is" in source


def test_the_privacy_claim_still_holds():
    """Whichever method runs, frames are examined and discarded on-device."""
    source = read(FOCUS_JS)
    assert "Frames stay on this device" in source
    # The capture element is never attached, so nothing else can read it.
    assert "document.createElement('video')" in source
    assert "appendChild" not in source
