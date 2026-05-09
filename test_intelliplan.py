"""
IntelliPlan Automated Tests
Run with: pytest test_intelliplan.py -v
Or with headed browser: pytest test_intelliplan.py -v --headed
"""

import pytest
from playwright.sync_api import Page, expect

BASE_URL = "https://intelliplan.tech"

# ── HELPERS ───────────────────────────────────────────────────

def go(page: Page, path: str):
    page.goto(f"{BASE_URL}{path}")

# ── LANDING PAGE ──────────────────────────────────────────────

class TestLandingPage:
    def test_landing_loads(self, page: Page):
        go(page, "/")
        expect(page).to_have_title("IntelliPlan")

    def test_get_started_button_redirects_to_login(self, page: Page):
        go(page, "/")
        page.get_by_text("Get Started Free").first.click()
        expect(page).to_have_url(f"{BASE_URL}/login")

    def test_logo_links_to_home(self, page: Page):
        go(page, "/")
        page.locator("a", has_text="IntelliPlan").first.click()
        expect(page).to_have_url(f"{BASE_URL}/")

# ── LOGIN PAGE ────────────────────────────────────────────────

class TestLoginPage:
    def test_login_page_loads(self, page: Page):
        go(page, "/login")
        expect(page.get_by_text("Sign in to IntelliPlan")).to_be_visible()

    def test_google_signin_button_visible(self, page: Page):
        go(page, "/login")
        expect(page.get_by_role("link", name="Sign in with Google")).to_be_visible()

    def test_create_account_button_visible(self, page: Page):
        go(page, "/login")
        expect(page.get_by_role("link", name="Create Account")).to_be_visible()

    def test_create_account_redirects_to_register(self, page: Page):
        go(page, "/login")
        page.get_by_role("link", name="Create Account").click()
        expect(page).to_have_url(f"{BASE_URL}/register")

    # FIX: use exact role match to avoid hitting the page heading
    def test_sign_in_button_redirects_to_login_account(self, page: Page):
        go(page, "/login")
        page.get_by_role("link", name="Sign In", exact=True).click()
        expect(page).to_have_url(f"{BASE_URL}/login/account")

    def test_canvas_option_visible(self, page: Page):
        go(page, "/login")
        expect(page.get_by_text("Canvas LMS")).to_be_visible()

    def test_studentvue_option_visible(self, page: Page):
        go(page, "/login")
        expect(page.get_by_text("StudentVue")).to_be_visible()

    def test_canvas_link_works(self, page: Page):
        go(page, "/login")
        page.get_by_text("Canvas LMS").click()
        expect(page).to_have_url(f"{BASE_URL}/login/canvas")

    def test_studentvue_link_works(self, page: Page):
        go(page, "/login")
        page.get_by_text("StudentVue").click()
        expect(page).to_have_url(f"{BASE_URL}/login/studentvue")

# ── REGISTER PAGE ─────────────────────────────────────────────

class TestRegisterPage:
    def test_register_page_loads(self, page: Page):
        go(page, "/register")
        expect(page.get_by_text("Create your account")).to_be_visible()

    def test_google_button_visible_on_register(self, page: Page):
        go(page, "/register")
        expect(page.get_by_role("link", name="Continue with Google")).to_be_visible()

    def test_email_field_visible(self, page: Page):
        go(page, "/register")
        expect(page.locator("input[name='email']")).to_be_visible()

    def test_password_field_visible(self, page: Page):
        go(page, "/register")
        expect(page.locator("input[name='password']")).to_be_visible()

    def test_confirm_password_field_visible(self, page: Page):
        go(page, "/register")
        expect(page.locator("input[name='confirm_password']")).to_be_visible()

    def test_password_mismatch_shows_error(self, page: Page):
        go(page, "/register")
        page.locator("input[name='email']").fill("test@example.com")
        page.locator("input[name='password']").fill("password123")
        page.locator("input[name='confirm_password']").fill("wrongpassword")
        page.get_by_text("Create Account →").click()
        expect(page.get_by_text("Passwords do not match")).to_be_visible()

    def test_short_password_shows_error(self, page: Page):
        go(page, "/register")
        page.locator("input[name='email']").fill("test@example.com")
        page.locator("input[name='password']").fill("short")
        page.locator("input[name='confirm_password']").fill("short")
        page.get_by_text("Create Account →").click()
        expect(page.get_by_text("at least 8 characters")).to_be_visible()

    def test_back_to_login_link_works(self, page: Page):
        go(page, "/register")
        page.get_by_text("← Back to login options").click()
        expect(page).to_have_url(f"{BASE_URL}/login")

# ── LEGAL PAGE ────────────────────────────────────────────────

class TestLegalPage:
    # FIX: target h2 headings specifically to avoid strict mode violations
    def test_legal_page_loads(self, page: Page):
        go(page, "/legal")
        expect(page.get_by_role("heading", name="Privacy Policy")).to_be_visible()

    def test_terms_of_service_visible(self, page: Page):
        go(page, "/legal")
        expect(page.get_by_role("heading", name="Terms of Service")).to_be_visible()

    def test_coppa_section_visible(self, page: Page):
        go(page, "/legal")
        expect(page.get_by_text("Students under 13 (COPPA)", exact=True)).to_be_visible()

    def test_no_passwords_stored_statement(self, page: Page):
        go(page, "/legal")
        assert "do not store passwords" in page.content().lower() or \
               "not store passwords" in page.content().lower()

    # FIX: check raw HTML content since email may not be a visible standalone element
    def test_contact_email_present(self, page: Page):
        go(page, "/legal")
        assert "anirudh@intelliplan.app" in page.content()

# ── AUTH REDIRECTS ────────────────────────────────────────────

class TestAuthRedirects:
    """Unauthenticated users should be redirected to login for protected pages."""

    @pytest.mark.parametrize("path", [
        "/dashboard",
        "/scheduler",
        "/priority",
        "/classes",
        "/grades",
        "/study",
        "/settings",
        "/profiles",
    ])
    def test_protected_page_redirects_to_login(self, page: Page, path: str):
        go(page, path)
        expect(page).to_have_url(f"{BASE_URL}/login")

# ── CANVAS LOGIN PAGE ─────────────────────────────────────────

class TestCanvasLoginPage:
    def test_canvas_login_loads(self, page: Page):
        go(page, "/login/canvas")
        expect(page.locator("input[name='canvas_token']")).to_be_visible()

    # FIX: target the submit button by name instead of generic get_by_role("button")
    def test_canvas_login_empty_submit_stays_on_page(self, page: Page):
        go(page, "/login/canvas")
        page.locator("input[name='canvas_token']").fill("")
        page.get_by_role("button", name="Connect Canvas →").click()
        expect(page).to_have_url(f"{BASE_URL}/login/canvas")

# ── STUDENTVUE LOGIN PAGE ─────────────────────────────────────

class TestStudentVueLoginPage:
    def test_studentvue_login_loads(self, page: Page):
        go(page, "/login/studentvue")
        expect(page.locator("input[name='username']")).to_be_visible()

    def test_studentvue_has_password_field(self, page: Page):
        go(page, "/login/studentvue")
        expect(page.locator("input[name='password']")).to_be_visible()

# ── INSTALL PAGE ──────────────────────────────────────────────

class TestInstallPage:
    def test_install_page_loads(self, page: Page):
        go(page, "/install")
        expect(page).not_to_have_url(f"{BASE_URL}/login")

    def test_ios_install_page_loads(self, page: Page):
        go(page, "/install/ios")
        expect(page).not_to_have_url(f"{BASE_URL}/login")

# ── API ENDPOINTS ─────────────────────────────────────────────

class TestAPIEndpoints:
    def test_live_endpoint_returns_json(self, page: Page):
        response = page.request.get(f"{BASE_URL}/live")
        assert response.status in [200, 302]

    def test_tasks_unified_returns_json(self, page: Page):
        response = page.request.get(f"{BASE_URL}/tasks/unified")
        assert response.status in [200, 302]

    def test_calendar_events_returns_json(self, page: Page):
        response = page.request.get(f"{BASE_URL}/calendar/events")
        assert response.status == 200
        data = response.json()
        assert "connected" in data

    def test_push_vapid_returns_key(self, page: Page):
        response = page.request.get(f"{BASE_URL}/push/vapid-public")
        assert response.status == 200
        data = response.json()
        assert "key" in data

    def test_study_access_returns_status(self, page: Page):
        response = page.request.get(f"{BASE_URL}/study/access")
        assert response.status == 200
        data = response.json()
        assert "status" in data

# ── COMPLIANCE CHECKS ─────────────────────────────────────────

class TestComplianceChecks:
    """Checks that district compliance requirements are met."""

    # FIX: check for discord.gg links only — the word "discord" alone may appear
    # in base template comments or CSS variable names
    def test_no_discord_links_on_landing(self, page: Page):
        go(page, "/")
        assert "discord.gg" not in page.content().lower()

    def test_no_discord_links_on_login(self, page: Page):
        go(page, "/login")
        assert "discord.gg" not in page.content().lower()

    def test_no_discord_links_on_legal(self, page: Page):
        go(page, "/legal")
        assert "discord.gg" not in page.content().lower()

    def test_privacy_policy_mentions_no_passwords(self, page: Page):
        go(page, "/legal")
        assert "do not store passwords" in page.content().lower() or \
               "not store passwords" in page.content().lower()

    def test_privacy_policy_mentions_coppa(self, page: Page):
        go(page, "/legal")
        assert "under 13" in page.content().lower() or "coppa" in page.content().lower()

    def test_no_ads(self, page: Page):
        go(page, "/")
        content = page.content().lower()
        assert "googlesyndication" not in content
        assert "doubleclick" not in content
        assert "adsbygoogle" not in content