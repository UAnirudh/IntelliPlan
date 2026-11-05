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


def test_the_active_page_keeps_the_app_navigation_in_place(client, student):
    """Opening Active must not move desktop navigation into a different bar."""
    html = client.get("/active").data.decode("utf-8", "ignore")
    assert 'class="app-side"' in html
    assert 'href="/active" data-nav-item="active" class="side-link active"' in html


def test_narrow_desktop_controls_stay_in_one_row_with_short_labels():
    """Resizing a desktop browser must not turn session actions into 2-3 rows."""
    source = read("Main_Project/templates/active.html")
    assert ".ipa-controls { display: flex; gap: .6rem; flex-wrap: nowrap;" in source
    assert ".ipa-controls .ipa-btn--primary { flex-basis: 100%; }" not in source
    assert 'class="ipa-btn-short">Done</span>' in source


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


def test_an_in_session_camera_stop_control_never_stops_the_study_timer():
    source = read(ACTIVE_JS)
    desktop = " ".join(read("Main_Project/templates/active.html").split())
    mobile = " ".join(read("Main_Project/templates/active_mobile.html").split())
    assert desktop.count('id="ipaFocusStop"') == 1
    assert mobile.count('id="ipaFocusStop"') == 1
    assert "focusStop.addEventListener('click', stopCameraCheckIn)" in source
    stop = source[source.find("function stopCameraCheckIn()"):]
    stop = stop[:stop.find("\n  }")]
    assert "$('ipaFocusToggle').checked = false" in stop
    assert "stopTracker()" in stop
    assert "IPA.running" not in stop and "pause()" not in stop and "openFinish" not in stop


def test_camera_status_is_announced_politely_on_both_active_pages():
    desktop = " ".join(read("Main_Project/templates/active.html").split())
    mobile = " ".join(read("Main_Project/templates/active_mobile.html").split())
    status = '<span id="ipaFocusStateText" role="status" aria-live="polite" aria-atomic="true">Not tracking</span>'
    assert status in desktop
    assert status in mobile


def test_camera_stop_is_hidden_even_if_permission_check_is_cancelled_before_tracker_exists():
    source = read(ACTIVE_JS)
    stop = source[source.find("function stopTracker()"):]
    stop = stop[:stop.find("\n  }")]
    assert stop.find("stopButton.hidden = true") < stop.find("if (!IPA.tracker) return")
    start = source[source.find("function startTracker()"):]
    start = start[:start.find("\n  }")]
    assert start.find("focusState.hidden = false") < start.find("api('/api/active/focus-permission')")


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



def test_settings_explains_the_camera_check_in_limitations():
    source = " ".join(read("Main_Project/templates/settings.html").split())
    assert "Depending on browser support" in source
    assert "face detection or look for movement" in source
    assert "cannot tell an empty chair from a still reader" in source
    assert "no check-in proves that you are studying" in source
    assert "notice when you have actually left" not in source
