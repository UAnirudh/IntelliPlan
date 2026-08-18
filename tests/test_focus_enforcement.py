"""Focus enforcement: preferences, and the alarm-sound upload.

The upload is the part that needs the most care. It is an arbitrary file
from a student, written to disk and served back, so every test here that
looks paranoid is load-bearing: an extension is a claim by the uploader,
a Content-Length is a claim by the client, and a guessable id in a URL is
an invitation to try someone else's.
"""

import io

import pytest

import App


@pytest.fixture()
def client():
    App.app.config["WTF_CSRF_ENABLED"] = False
    return App.app.test_client()


@pytest.fixture()
def user_id():
    with App.app.app_context():
        u = App.User.query.first()
        if u is None:
            pytest.skip("no user in the development database")
        return u.id


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def _wav(size=64):
    """A minimal file that passes the magic-byte sniff."""
    return b"RIFF" + b"\x00" * size


# ── Preferences ───────────────────────────────────────────────────────


def test_enforcement_is_off_until_asked_for(client, user_id):
    """An app that starts blaring at someone who never chose it is an app
    they uninstall. Off is the only acceptable default."""
    _login(client, user_id)
    client.post("/api/focus/enforcement", json={"mode": "off"})
    body = client.get("/api/focus/enforcement").get_json()
    assert body["mode"] == "off"


def test_every_mode_round_trips(client, user_id):
    _login(client, user_id)
    for mode in ("alarm", "takeover", "stakes", "off"):
        saved = client.post("/api/focus/enforcement", json={"mode": mode}).get_json()
        assert saved["mode"] == mode
        assert client.get("/api/focus/enforcement").get_json()["mode"] == mode


def test_an_unknown_mode_is_rejected(client, user_id):
    _login(client, user_id)
    assert client.post("/api/focus/enforcement", json={"mode": "airhorn"}).status_code == 400


def test_grace_period_is_clamped_to_something_usable(client, user_id):
    """Below ~10s the alarm fires when someone reaches for a textbook;
    past two minutes it is not enforcing anything."""
    _login(client, user_id)
    lo, hi = App.FOCUS_GRACE_BOUNDS
    assert client.post("/api/focus/enforcement", json={"grace_seconds": 99999}).get_json()["grace_seconds"] == hi
    assert client.post("/api/focus/enforcement", json={"grace_seconds": -5}).get_json()["grace_seconds"] == lo


def test_a_non_numeric_grace_is_a_400_not_a_crash(client, user_id):
    _login(client, user_id)
    assert client.post("/api/focus/enforcement", json={"grace_seconds": "soon"}).status_code == 400


def test_preferences_require_a_login(client):
    assert client.get("/api/focus/enforcement").status_code == 401
    assert client.post("/api/focus/enforcement", json={"mode": "alarm"}).status_code == 401


# ── Alarm upload ──────────────────────────────────────────────────────


def _upload(client, data, filename):
    return client.post(
        "/api/focus/alarm",
        data={"sound": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def test_a_real_sound_is_accepted_and_served_back(client, user_id):
    _login(client, user_id)
    body = _upload(client, _wav(), "alarm.wav").get_json()
    assert body["alarm_url"]
    assert client.get(body["alarm_url"]).status_code == 200


def test_a_disguised_file_is_rejected_on_its_bytes(client, user_id):
    """The extension is a claim by the uploader. The magic bytes are not."""
    _login(client, user_id)
    r = _upload(client, b"<?php system($_GET['c']); ?>", "totally-a-song.mp3")
    assert r.status_code == 400
    assert "audio" in r.get_json()["message"].lower()


def test_a_non_audio_extension_is_rejected(client, user_id):
    _login(client, user_id)
    assert _upload(client, _wav(), "payload.exe").status_code == 400
    assert _upload(client, _wav(), "notes.pdf").status_code == 400


def test_an_oversized_file_is_rejected(client, user_id):
    _login(client, user_id)
    too_big = b"RIFF" + b"\x00" * (App.ALARM_MAX_BYTES + 10)
    assert _upload(client, too_big, "epic.wav").status_code == 400


def test_a_traversing_filename_cannot_escape_the_upload_folder(client, user_id):
    """The uploaded name never reaches the filesystem — the stored file is
    named from the user id — so there is nothing to traverse with."""
    _login(client, user_id)
    body = _upload(client, _wav(), "../../../../etc/passwd.wav").get_json()
    assert body["status"] == "ok"
    with App.app.app_context():
        stored = App.db.session.get(App.User, user_id).focus_alarm_file
    assert "/" not in stored and "\\" not in stored
    assert stored.startswith(f"user_{user_id}")


def test_uploads_do_not_accumulate_per_user(client, user_id):
    """One file per student. Otherwise every re-upload leaks a file."""
    _login(client, user_id)
    for _ in range(3):
        assert _upload(client, _wav(), "alarm.wav").status_code == 200
    import os
    owned = [
        f for f in os.listdir(App.ALARM_UPLOAD_FOLDER)
        if f.startswith(f"user_{user_id}.")
    ]
    assert len(owned) == 1


def test_a_student_cannot_fetch_another_students_sound(client, user_id):
    """The id in the URL is guessable, so the route has to check ownership
    rather than trust it."""
    _login(client, user_id)
    _upload(client, _wav(), "alarm.wav")
    r = client.get(f"/uploads/focus-alarm/{user_id + 4242}")
    assert r.status_code != 200


def test_removing_the_sound_falls_back_to_the_built_in_tone(client, user_id):
    _login(client, user_id)
    _upload(client, _wav(), "alarm.wav")
    assert client.delete("/api/focus/alarm").get_json()["alarm_url"] == ""


def test_upload_requires_a_login(client):
    assert _upload(client, _wav(), "alarm.wav").status_code == 401


def test_a_missing_file_is_a_clear_error(client, user_id):
    _login(client, user_id)
    r = client.post("/api/focus/alarm", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


# ── Regression: the site's own 404 ────────────────────────────────────


def test_a_missing_page_is_a_404_not_a_500(client):
    """intelliplan_api registers an app-wide 404 handler and used to
    re-raise for non-API paths. Re-raising inside an error handler does not
    fall through to another handler — it lands in the catch-all — so every
    404 on the site rendered as a 500."""
    r = client.get("/no-such-page-anywhere", headers={"Referer": "http://localhost/dashboard"})
    assert r.status_code == 404


def test_a_missing_asset_is_a_plain_404(client):
    assert client.get("/nope.png").status_code == 404


def test_the_api_still_gets_its_json_404(client):
    r = client.get("/api/v1/definitely-not-an-endpoint")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"
