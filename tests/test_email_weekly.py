"""The weekly newsletter and the feedback blast.

The weekly issue ships with no human reading it first, so the tests that
matter are the ones asserting what CANNOT reach a student: an internal
commit subject, a raw scope name, a secret rotation. The allow-list is the
safety mechanism and these pin it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest

import App as app_module
from intelliplan.email import campaigns, changelog


@pytest.fixture
def ctx():
    with app_module.app.app_context():
        yield


@pytest.fixture
def resend(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-key-not-real")
    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "IntelliPlan, 1 Test Way")
    # The batch delay is real time, and these tests walk the whole eligible
    # list. Keeping it would add minutes to the suite to prove nothing.
    monkeypatch.setattr(campaigns, "BATCH_DELAY_SECONDS", 0)
    captured: list[dict] = []

    class FakeResponse:
        def read(self):
            return json.dumps({"id": "msg_weekly_0001"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured.append(json.loads(req.data.decode()))
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        yield captured


def make_user(**overrides):
    defaults = {
        "email": f"wk-{uuid.uuid4().hex[:10]}@example.test",
        "birth_year": 2000,
        "parent_consent_granted": True,
        "role": "student",
        "marketing_emails_opt_in": True,
        "marketing_opt_in_at": datetime(2026, 1, 1),
        "password_hash": "x",
    }
    defaults.update(overrides)
    user = app_module.User(**defaults)
    app_module.db.session.add(user)
    app_module.db.session.commit()
    return user


# -- The changelog filter --------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        "refactor(scheduler): extract the placement loop",
        "chore(deps): bump flask to 3.1.3",
        "test(email): add eligibility coverage",
        "ci: pin the runner image",
        "docs(readme): fix a typo in the setup steps",
        "feat(glue): rewire the internal dispatcher",
        "feat: no scope at all here",
        "random text that is not a commit convention",
        "fix(web): rotate the leaked api_key",
        "feat(web): internal admin tooling for support",
        "fix(web): revert the broken deploy",
        "feat(web): x",
    ],
)
def test_these_never_reach_a_student(subject):
    assert changelog.humanise(subject) is None, f"{subject!r} leaked into the newsletter"


@pytest.mark.parametrize(
    "subject,expected_tag",
    [
        ("feat(desktop): update the app without making anyone reinstall it", "New · Desktop app"),
        ("fix(scheduler): stop double-booking the lunch hour", "Fixed · Scheduler"),
        ("feat(canvas): import assignment point values", "New · Canvas"),
        ("feat(studentvue): show missing work inline", "New · StudentVue"),
    ],
)
def test_user_facing_commits_are_translated(subject, expected_tag):
    item = changelog.humanise(subject)
    assert item is not None
    assert item["tag"] == expected_tag
    assert item["title"][0].isupper()


def test_recent_changes_falls_back_to_the_snapshot(monkeypatch):
    """Production has no .git, so the snapshot is the only source there."""
    monkeypatch.setattr(changelog, "_git_log_since", lambda since: [])
    items = changelog.recent_changes(since=datetime(2000, 1, 1), limit=5)
    assert isinstance(items, list)
    for item in items:
        assert set(item) == {"tag", "title", "body"}


# -- Issue generation ------------------------------------------------


def test_an_issue_is_deterministic_for_a_week(ctx):
    """Nobody reviews this, so the preview must be exactly what is sent."""
    when = datetime(2026, 3, 11)
    assert campaigns.generate_weekly_issue(when) == campaigns.generate_weekly_issue(when)


def test_a_quiet_week_still_produces_a_usable_issue(ctx, monkeypatch):
    monkeypatch.setattr(changelog, "recent_changes", lambda **kw: [])
    issue = campaigns.generate_weekly_issue(datetime(2026, 3, 11))
    assert len(issue["features"]) == 1, "the how-to tip should still be there"
    assert issue["tip"]["title"]
    assert len(issue["stats"]) == 3


def test_consecutive_weeks_get_different_content(ctx):
    a = campaigns.generate_weekly_issue(datetime(2026, 3, 4))
    b = campaigns.generate_weekly_issue(datetime(2026, 3, 11))
    assert a["tip"]["title"] != b["tip"]["title"]
    assert a["email_key"] != b["email_key"]


def test_the_weekly_key_is_one_per_iso_week():
    assert campaigns.weekly_key(datetime(2026, 3, 9)) == campaigns.weekly_key(datetime(2026, 3, 13))
    assert campaigns.weekly_key(datetime(2026, 3, 9)) != campaigns.weekly_key(datetime(2026, 3, 16))


def test_the_weekly_issue_renders(ctx, monkeypatch):
    from intelliplan.email import templates

    monkeypatch.setenv("MARKETING_POSTAL_ADDRESS", "IntelliPlan, 1 Test Way")
    issue = campaigns.generate_weekly_issue(datetime(2026, 3, 11))
    context = templates.build_context(
        user=None,
        unsubscribe_url="https://x.test/u",
        preheader=issue["preheader"],
        **{k: issue[k] for k in ("issue_label", "headline", "intro", "features", "tip", "stats")},
    )
    rendered = templates.render("newsletter", issue["subject"], context)
    assert issue["tip"]["title"] in rendered.html
    assert "1 Test Way" in rendered.html
    assert "{{" not in rendered.html, "an unrendered Jinja token survived"


# -- Sending ---------------------------------------------------------


def test_the_weekly_send_only_reaches_the_eligible(ctx, resend):
    """A suppressed address must not receive an unattended blast."""
    from intelliplan.email.eligibility import suppress

    blocked = make_user()
    suppress(blocked.email)

    campaigns.send_weekly_newsletter(now=datetime(2026, 4, 8), dry_run=False)
    assert [p for p in resend if p["to"] == [blocked.email]] == []


def test_a_repeat_cron_fire_does_not_send_twice(ctx, resend):
    when = datetime(2026, 5, 13)
    campaigns.send_weekly_newsletter(now=when, dry_run=False)
    before = len(resend)
    campaigns.send_weekly_newsletter(now=when, dry_run=False)
    assert len(resend) == before, "the second fire sent a duplicate issue"


def test_the_weekly_send_refuses_without_a_postal_address(ctx, resend, monkeypatch):
    monkeypatch.delenv("MARKETING_POSTAL_ADDRESS", raising=False)
    summary = campaigns.send_weekly_newsletter(now=datetime(2026, 6, 10), dry_run=False)
    assert summary.get("error")
    assert resend == []


def test_a_dry_run_sends_nothing(ctx, resend):
    summary = campaigns.send_weekly_newsletter(now=datetime(2026, 7, 8), dry_run=True)
    assert summary["dry_run"] is True
    assert resend == []


# -- The feedback blast ----------------------------------------------


def test_the_feedback_blast_defaults_to_a_dry_run(ctx, resend):
    summary = campaigns.send_feedback_now()
    assert summary["dry_run"] is True
    assert resend == []


def test_the_feedback_blast_skips_activity_but_not_consent(ctx, resend):
    """The whole point: reach opted-in people regardless of how long ago they
    signed up or whether they came back, but nobody who said no."""
    fresh = make_user()
    child = make_user(
        birth_year=datetime.utcnow().year - 10, parent_consent_granted=False
    )
    optout = make_user(marketing_emails_opt_in=False, marketing_opt_in_at=None)

    campaigns.send_feedback_now(dry_run=False)
    sent_to = {p["to"][0] for p in resend}

    assert fresh.email in sent_to, "a brand-new opted-in user should be reached"
    assert child.email not in sent_to, "COPPA: a child was mailed"
    assert optout.email not in sent_to, "someone who never opted in was mailed"
