"""The published Privacy Policy change, v1 to v2.

Microsoft Clarity — session replay and heatmaps — had been loading on every
page while §1 stated in writing that IntelliPlan does not use session replay.
The tracker was removed rather than disclosed, so the original promise is
true again.

Telling people is the other half of that. The notice mechanism was built for
exactly this and then left dormant through two material edits to the policy,
which meant the document changed and nobody was told. These tests hold the
published version to what actually changed, and hold the summary to the part
that is uncomfortable to say.
"""

import pytest

import App
import policy_versions
from App import db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
        yield c
    App.limiter.enabled = True


def privacy_v2():
    return next(v for v in policy_versions.PRIVACY_VERSIONS if v["version"] == 2)


def legal_html(client):
    return client.get("/legal").data.decode("utf-8", "ignore")


# ── The change is actually published ─────────────────────────

def test_the_privacy_policy_is_past_its_baseline(client):
    """Two material edits shipped while this still read v1, so the notice
    never fired and the change went out silently."""
    assert policy_versions.current_version(policy_versions.PRIVACY) == 2


def test_an_existing_user_is_asked_to_read_it(client):
    pending = client.get("/api/policy/pending").get_json()["pending"]
    privacy = [p for p in pending if p["doc"] == "privacy"]
    assert len(privacy) == 1
    assert privacy[0]["from_version"] == 1
    assert privacy[0]["version"] == 2


def test_accepting_it_settles_the_notice(client):
    assert client.post("/api/policy/acknowledge",
                       json={"doc": "privacy", "version": 2}).status_code == 200
    assert client.get("/api/policy/pending").get_json()["pending"] == []


def test_the_terms_are_untouched_and_prompt_nobody(client):
    """Only the Privacy Policy changed. Bundling an unrelated document into
    the same notice would train people to click through both."""
    pending = client.get("/api/policy/pending").get_json()["pending"]
    assert [p for p in pending if p["doc"] == "terms"] == []


# ── The summary says the uncomfortable part ──────────────────

def test_the_summary_admits_the_policy_had_been_inaccurate(client):
    """The tempting version of this notice mentions only the improvement.
    What changed is that a statement we were already making became true
    again, and that is the part a reader deserves."""
    summary = " ".join(privacy_v2()["summary"]).lower()
    assert "not accurate" in summary
    assert "quietly" in summary


def test_the_summary_names_the_tool_that_was_removed(client):
    summary = " ".join(privacy_v2()["summary"])
    assert "Microsoft Clarity" in summary


def test_the_summary_says_nothing_new_is_collected(client):
    """This change only removes and clarifies. Saying so is what stops a
    privacy notice reading as a warning."""
    assert any("Nothing new is collected" in s for s in privacy_v2()["summary"])


# ── The verbatim text matches the live policy ────────────────

def test_the_quoted_new_wording_is_what_the_policy_now_says(client):
    """A summary is an interpretation; the binding text is the text. If the
    quoted clause drifts from the page, the notice is describing a document
    that does not exist."""
    html = legal_html(client)
    telemetry = next(c for c in privacy_v2()["clauses"]
                     if c["heading"].startswith("1."))
    # The page is HTML with entities and markup, so compare on distinctive
    # phrases rather than the whole run of text.
    for phrase in ("recorded on our own servers",
                   "No third-party analytics script runs in your browser"):
        assert phrase in telemetry["after"]
        assert phrase in html


def test_the_quoted_old_wording_is_no_longer_on_the_page(client):
    telemetry = next(c for c in privacy_v2()["clauses"]
                     if c["heading"].startswith("1."))
    assert "keystroke loggers, or third-party advertising trackers" in telemetry["before"]
    assert "keystroke loggers, or third-party advertising trackers" not in legal_html(client)


def test_the_new_cookie_section_is_quoted_and_present(client):
    cookies = next(c for c in privacy_v2()["clauses"] if "Cookies" in c["heading"])
    assert "no analytics, advertising or tracking cookies" in cookies["after"]
    assert 'id="p-cookies"' in legal_html(client)


def test_every_clause_carries_both_sides(client):
    """"Now reads" without "previously" leaves a reader unable to see what
    moved, which is the only reason to show verbatim text at all."""
    for clause in privacy_v2()["clauses"]:
        assert clause.get("heading")
        assert clause.get("before")
        assert clause.get("after")


# ── The claim the notice rests on ────────────────────────────

@pytest.mark.parametrize("path", ["/", "/legal", "/pricing", "/cookies"])
def test_no_third_party_analytics_script_is_served(client, path):
    """The notice says no third-party analytics script runs. That has to be
    true on every page, not just the one the policy is written on."""
    page = client.get(path).data
    assert b"clarity.ms" not in page
    assert b"googletagmanager" not in page
