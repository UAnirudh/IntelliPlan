"""School outreach, the manual-entry survey, the age gate, and policy notices.

Four things that share one property: each exists because the product used to
lose information it needed. A student blocked by their school hit a wall and
we never learned which school. Someone typing classes in was never asked why.
A Google sign-in skipped the age question the password form insists on. And a
policy change reached nobody.
"""

import json
import re

import pytest

import App
import policy_versions
from App import (ManualEntryReason, PolicyAcknowledgement, SchoolOutreachLead,
                 User, db)


@pytest.fixture
def client(monkeypatch):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            SchoolOutreachLead.query.delete()
            ManualEntryReason.query.delete()
            PolicyAcknowledgement.query.delete()
            User.query.filter(User.email.like("outreach+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            SchoolOutreachLead.query.delete()
            ManualEntryReason.query.delete()
            PolicyAcknowledgement.query.delete()
            User.query.filter(User.email.like("outreach+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


def signed_in(client, email="outreach+a@example.com", **kwargs):
    with App.app.app_context():
        user = User(email=email, password_hash="", name="Tester", **kwargs)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return uid


# ── Pricing structured data ──────────────────────────────────

def _pricing_jsonld(client):
    html = client.get("/pricing").data.decode("utf-8", "ignore")
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                        html, re.S)
    for block in blocks:
        data = json.loads(block)
        if data.get("@type") == "SoftwareApplication":
            return data
    return None


def test_the_pricing_listing_is_software_not_merchandise(client):
    """Typed as Product, Google validated it as a merchant listing and asked
    for shipping and returns — fields a free web app cannot honestly fill."""
    html = client.get("/pricing").data.decode("utf-8", "ignore")
    assert '"@type": "Product"' not in html
    assert _pricing_jsonld(client) is not None


@pytest.mark.parametrize("field", ["name", "image", "description", "offers"])
def test_the_fields_google_flagged_are_present(client, field):
    assert _pricing_jsonld(client).get(field)


def test_the_offer_states_its_availability_and_price(client):
    offers = _pricing_jsonld(client)["offers"]
    assert offers["availability"] == "https://schema.org/InStock"
    assert offers["price"] == "0"
    assert offers["priceCurrency"] == "USD"


def test_the_listing_parses_as_json(client):
    """A trailing comma here silently costs every rich result on the page."""
    html = client.get("/pricing").data.decode("utf-8", "ignore")
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                            html, re.S):
        json.loads(block)


# ── School outreach ──────────────────────────────────────────

def test_a_school_name_is_enough_to_submit(client):
    """Most students have no idea who runs their LMS. Demanding an IT
    contact would lose the ones who can still name their school."""
    r = client.post("/api/school-outreach", json={"school_name": "Lincoln High"})
    assert r.status_code == 200
    with App.app.app_context():
        assert SchoolOutreachLead.query.count() == 1


def test_a_submission_without_a_school_is_refused(client):
    assert client.post("/api/school-outreach", json={}).status_code == 400


def test_the_full_details_are_stored(client):
    client.post("/api/school-outreach", json={
        "school_name": "Lincoln High",
        "district": "Springfield USD",
        "platform": "blackboard",
        "school_url": "https://learn.lincoln.edu",
        "it_contact_name": "Ms. Rivera",
        "it_contact_email": "it@lincoln.edu",
        "notes": "Sign-in fails with invalid client_id",
    })
    with App.app.app_context():
        lead = SchoolOutreachLead.query.one()
        assert lead.district == "Springfield USD"
        assert lead.platform == "blackboard"
        assert lead.it_contact_email == "it@lincoln.edu"
        assert lead.status == "new"


def test_an_unknown_platform_is_recorded_as_other(client):
    client.post("/api/school-outreach",
                json={"school_name": "X", "platform": "sakai"})
    with App.app.app_context():
        assert SchoolOutreachLead.query.one().platform == "other"


def test_a_malformed_contact_email_is_refused(client):
    r = client.post("/api/school-outreach",
                    json={"school_name": "X", "it_contact_email": "not-an-email"})
    assert r.status_code == 400


def test_a_signed_in_student_does_not_retype_their_email(client):
    signed_in(client, email="outreach+b@example.com")
    client.post("/api/school-outreach", json={"school_name": "Lincoln High"})
    with App.app.app_context():
        assert SchoolOutreachLead.query.one().student_email == "outreach+b@example.com"


def test_a_lead_belongs_to_the_account_that_filed_it(client):
    uid = signed_in(client, email="outreach+c@example.com")
    client.post("/api/school-outreach", json={"school_name": "Lincoln High"})
    with App.app.app_context():
        assert SchoolOutreachLead.query.one().user_id == uid


# ── Manual-entry survey ──────────────────────────────────────

def test_the_survey_is_asked_before_it_is_answered(client):
    assert client.get("/api/manual-entry-reason").get_json()["should_ask"] is True


def test_the_survey_is_not_asked_twice(client):
    client.post("/api/manual-entry-reason", json={"reason": "prefer_manual"})
    assert client.get("/api/manual-entry-reason").get_json()["should_ask"] is False


def test_someone_who_already_told_us_about_their_school_is_not_surveyed(client):
    """They have already given us the more useful answer."""
    client.post("/api/school-outreach", json={"school_name": "Lincoln High"})
    assert client.get("/api/manual-entry-reason").get_json()["should_ask"] is False


def test_an_invented_reason_is_refused(client):
    r = client.post("/api/manual-entry-reason", json={"reason": "because"})
    assert r.status_code == 400


@pytest.mark.parametrize("reason", ["connect_error", "school_not_approved",
                                    "platform_unsupported"])
def test_a_school_shaped_problem_leads_to_the_outreach_form(client, reason):
    r = client.post("/api/manual-entry-reason", json={"reason": reason})
    assert r.get_json()["prompt_outreach"] is True


@pytest.mark.parametrize("reason", ["prefer_manual", "no_school_account"])
def test_a_personal_preference_does_not(client, reason):
    """Someone who simply prefers typing has no school for us to chase."""
    r = client.post("/api/manual-entry-reason", json={"reason": reason})
    assert r.get_json()["prompt_outreach"] is False


def test_the_reason_and_its_detail_are_stored(client):
    client.post("/api/manual-entry-reason", json={
        "reason": "connect_error", "platform": "blackboard",
        "detail": "invalid client_id after login",
    })
    with App.app.app_context():
        row = ManualEntryReason.query.one()
        assert row.reason == "connect_error"
        assert row.platform == "blackboard"
        assert "invalid client_id" in row.detail


# ── Age gate ─────────────────────────────────────────────────

def test_a_google_signup_without_a_birth_year_is_sent_to_the_age_step(client):
    with App.app.app_context():
        user = User(email="outreach+g@example.com", password_hash="",
                    google_id="g-age-1")
        db.session.add(user)
        db.session.commit()
        assert App._needs_age_gate(user) is True
        assert App._post_google_destination(user, "/command-center").startswith(
            "/account/age")


def test_a_user_whose_age_we_know_is_not_asked_again(client):
    with App.app.app_context():
        user = User(email="outreach+h@example.com", password_hash="",
                    google_id="g-age-2", birth_year=2006)
        db.session.add(user)
        db.session.commit()
        assert App._needs_age_gate(user) is False
        assert App._post_google_destination(user, "/command-center") == "/command-center"


def test_an_adult_passes_straight_through(client):
    uid = signed_in(client, email="outreach+i@example.com", google_id="g-age-3")
    r = client.post("/account/age", data={"birth_year": "2006"})
    assert r.status_code == 302
    with App.app.app_context():
        user = db.session.get(User, uid)
        assert user.birth_year == 2006
        assert user.parent_consent_granted is True


def test_an_under_13_signup_needs_a_parent_email(client):
    signed_in(client, email="outreach+j@example.com", google_id="g-age-4")
    r = client.post("/account/age", data={"birth_year": "2016"})
    assert b"parent or guardian" in r.data


def test_an_under_13_account_is_held_until_a_parent_approves(client):
    uid = signed_in(client, email="outreach+k@example.com", google_id="g-age-5")
    r = client.post("/account/age", data={"birth_year": "2016",
                                          "parent_email": "parent@example.com"})
    assert r.headers["Location"] == "/account/age/pending"
    with App.app.app_context():
        user = db.session.get(User, uid)
        assert user.parent_consent_granted is False
        assert user.parent_consent_token
        # COPPA: no marketing to a child without verified parental consent.
        assert user.marketing_emails_opt_in is False


def test_a_parent_cannot_be_the_child(client):
    signed_in(client, email="outreach+l@example.com", google_id="g-age-6")
    r = client.post("/account/age", data={"birth_year": "2016",
                                          "parent_email": "outreach+l@example.com"})
    assert b"different from your own" in r.data


@pytest.mark.parametrize("bad", ["abcd", "", "3025"])
def test_an_unusable_birth_year_is_refused(client, bad):
    signed_in(client, email="outreach+m@example.com", google_id="g-age-7")
    r = client.post("/account/age", data={"birth_year": bad})
    assert b"valid birth year" in r.data


def test_the_age_step_cannot_be_used_as_an_open_redirect(client):
    signed_in(client, email="outreach+n@example.com", google_id="g-age-8")
    r = client.post("/account/age",
                    data={"birth_year": "2006", "next": "//evil.example.com"})
    assert r.headers["Location"] == "/command-center"


@pytest.mark.parametrize("raw,expected", [
    ("/dashboard", "/dashboard"),
    ("//evil.com", "/command-center"),
    ("https://evil.com", "/command-center"),
    ("", "/command-center"),
    ("\\\\evil.com", "/command-center"),
])
def test_only_a_same_site_path_survives(raw, expected):
    assert App._safe_next_path(raw) == expected


# ── Policy acknowledgement ───────────────────────────────────

@pytest.fixture
def new_privacy_version():
    """Publish a version so there is something to acknowledge."""
    policy_versions.PRIVACY_VERSIONS.append({
        "version": 2,
        "effective": "2026-09-01",
        "summary": ["We now record which school you ask us to contact."],
        "clauses": [{"heading": "3. Information we collect",
                     "before": "We collect your name and email.",
                     "after": "We collect your name, email, and school details."}],
    })
    yield
    policy_versions.PRIVACY_VERSIONS[:] = [
        v for v in policy_versions.PRIVACY_VERSIONS if v["version"] != 2
    ]


def test_an_unchanged_policy_prompts_nobody(client):
    """Shipping this must not confront every existing user with a notice
    about a document that has not actually changed for them."""
    assert client.get("/api/policy/pending").get_json()["pending"] == []


def test_a_new_version_is_surfaced_with_summary_and_verbatim_text(
        client, new_privacy_version):
    pending = client.get("/api/policy/pending").get_json()["pending"]
    assert len(pending) == 1

    notice = pending[0]
    assert notice["doc"] == "privacy"
    assert notice["version"] == 2
    assert notice["summary"] == ["We now record which school you ask us to contact."]
    assert notice["clauses"][0]["before"] == "We collect your name and email."
    assert notice["clauses"][0]["after"] == \
        "We collect your name, email, and school details."


def test_accepting_clears_the_notice(client, new_privacy_version):
    r = client.post("/api/policy/acknowledge", json={"doc": "privacy", "version": 2})
    assert r.status_code == 200
    assert client.get("/api/policy/pending").get_json()["pending"] == []


def test_the_acceptance_is_recorded_as_evidence(client, new_privacy_version):
    signed_in(client, email="outreach+p@example.com")
    client.post("/api/policy/acknowledge", json={"doc": "privacy", "version": 2})
    with App.app.app_context():
        row = PolicyAcknowledgement.query.filter_by(doc="privacy").one()
        assert row.version == 2
        assert row.acknowledged_at is not None


def test_a_version_that_does_not_exist_is_refused(client):
    r = client.post("/api/policy/acknowledge", json={"doc": "privacy", "version": 99})
    assert r.status_code == 400


def test_an_unknown_document_is_refused(client):
    r = client.post("/api/policy/acknowledge", json={"doc": "cookies", "version": 1})
    assert r.status_code == 400


def test_missing_two_updates_shows_both_in_order(client, new_privacy_version):
    policy_versions.PRIVACY_VERSIONS.append({
        "version": 3, "effective": "2026-10-01",
        "summary": ["We added a data export tool."], "clauses": [],
    })
    try:
        notice = client.get("/api/policy/pending").get_json()["pending"][0]
        assert notice["version"] == 3
        assert notice["summary"] == [
            "We now record which school you ask us to contact.",
            "We added a data export tool.",
        ]
    finally:
        policy_versions.PRIVACY_VERSIONS[:] = [
            v for v in policy_versions.PRIVACY_VERSIONS if v["version"] != 3
        ]


def test_re_accepting_an_older_version_does_not_undo_a_newer_one(
        client, new_privacy_version):
    client.post("/api/policy/acknowledge", json={"doc": "privacy", "version": 2})
    client.post("/api/policy/acknowledge", json={"doc": "privacy", "version": 1})
    assert client.get("/api/policy/pending").get_json()["pending"] == []


def test_the_notice_markup_ships_on_every_page(client):
    for path in ("/", "/connect", "/pricing"):
        assert b"ipPolicyNotice" in client.get(path).data


# ── Accessibility ────────────────────────────────────────────
#
# Reviewed against the bundled Apple HIG guidance. These pin the fixes so a
# later edit cannot quietly undo them.

def test_the_policy_modal_announces_itself_as_one(client):
    html = client.get("/").data.decode("utf-8", "ignore")
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="ipPolicyTitle"' in html


def test_the_policy_modal_traps_keyboard_focus(client):
    """Focus escaping to the page behind leaves a keyboard user stranded on
    content they cannot see past."""
    html = client.get("/").data.decode("utf-8", "ignore")
    assert "function trapFocus" in html
    assert "document.documentElement.style.overflow = 'hidden'" in html


def test_the_scroll_gate_is_measured_after_layout(client):
    """scrollHeight read before paint under-reports, which would leave a
    short notice permanently un-acceptable."""
    html = client.get("/").data.decode("utf-8", "ignore")
    assert "requestAnimationFrame(checkScrolled)" in html


def test_the_scroll_hint_is_announced(client):
    html = client.get("/").data.decode("utf-8", "ignore")
    assert 'id="ipPolicyHint" class="ip-policy-hint" role="status" aria-live="polite"' in html


def test_the_survey_groups_its_options_for_screen_readers(client):
    """Six unattached labels do not tell a screen reader user what is being
    asked; a fieldset with a legend does."""
    html = client.get("/connect").data.decode("utf-8", "ignore")
    assert "<fieldset" in html
    assert "<legend" in html


def test_form_errors_are_announced(client):
    html = client.get("/connect").data.decode("utf-8", "ignore")
    assert 'id="ipoMsg" role="alert"' in html
    assert 'id="ipWhyMsg" role="alert"' in html


def test_the_required_field_says_so_in_words(client):
    """An asterisk carries no meaning to a screen reader and colour carries
    none to anyone."""
    html = client.get("/connect").data.decode("utf-8", "ignore")
    assert "(required)" in html
    assert 'aria-required="true"' in html


def test_the_submit_button_waits_for_the_data_it_requires(client):
    html = client.get("/connect").data.decode("utf-8", "ignore")
    assert "function ipOutreachValidate" in html
    assert 'id="ipoBtn" disabled' in html


def test_touch_targets_clear_the_mobile_minimum(client):
    """44px is the mobile floor; the survey rows and buttons were ~30px."""
    html = client.get("/connect").data.decode("utf-8", "ignore")
    assert html.count("min-height:44px") >= 3


def test_the_age_gate_labels_and_announces_its_error(client):
    with App.app.app_context():
        user = User(email="outreach+a11y@example.com", password_hash="",
                    google_id="g-a11y")
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True

    html = client.post("/account/age", data={"birth_year": "nope"}).data.decode(
        "utf-8", "ignore")
    assert 'role="alert"' in html
    assert 'for="birthYear"' in html
    # Numeric keyboard and autofill, per the data-entry guidance.
    assert 'inputmode="numeric"' in html
    assert 'autocomplete="bday-year"' in html
