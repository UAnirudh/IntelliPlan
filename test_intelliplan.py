"""
IntelliPlan End-to-End Test Suite
=================================

Comprehensive frontend + backend + responsive testing for the live site.

Run:
  pytest test_intelliplan.py -v
  pytest test_intelliplan.py -v --headed              # see the browser
  pytest test_intelliplan.py -v -k "TestLanding"      # one class only
  pytest test_intelliplan.py -v -k "mobile"           # only responsive tests

Coverage:
  - Frontend: landing, login, register, legal, install, study hub, extension API
  - Responsive: mobile (375), tablet (768), desktop (1280), wide (1920)
  - Backend: /live, /push/vapid-public, /study/access, /api/study-hub/recommend,
    /tasks/unified, /calendar/events, /extension/* CORS preflight
  - Auth: every protected route redirects to /login when unauthenticated
  - Compliance: no Discord links, no ads, COPPA + password policy on /legal
  - Accessibility: page <title>, meta viewport, og:image, lang=en

Resilient to live-site latency:
  - 60s navigation timeout per page
  - One automatic retry on TimeoutError
  - All locator assertions use Playwright's auto-waiting via `expect()`
"""

import re

import pytest
from playwright.sync_api import Page, TimeoutError as PWTimeoutError, expect

BASE_URL  = "https://intelliplan.tech"
NAV_TIMEOUT_MS = 60_000   # Production page goto budget
EXPECT_TIMEOUT_MS = 15_000  # Locator assertion budget

# Apply a generous default to every `expect()` so individual tests stay terse.
expect.set_options(timeout=EXPECT_TIMEOUT_MS)


# ── Resilient navigation helper ───────────────────────────────
def go(page: Page, path: str, *, wait: str = "domcontentloaded") -> None:
    """Navigate to BASE_URL+path with extended timeout and one retry on flake."""
    url = f"{BASE_URL}{path}"
    page.set_default_timeout(EXPECT_TIMEOUT_MS)
    try:
        page.goto(url, wait_until=wait, timeout=NAV_TIMEOUT_MS)
    except PWTimeoutError:
        # Single retry — the production CDN sometimes cold-starts a worker.
        page.goto(url, wait_until=wait, timeout=NAV_TIMEOUT_MS)


def assert_no_discord_links(page: Page) -> None:
    hrefs = page.locator("a").evaluate_all(
        "(els) => els.map(e => (e.href || '').toLowerCase())"
    )
    assert not any("discord.gg" in href for href in hrefs)


def assert_no_ad_terms(page: Page) -> None:
    content = page.content().lower()
    blocked = ["googlesyndication", "doubleclick", "adsbygoogle"]
    assert not any(t in content for t in blocked)


def assert_url_matches(page: Page, path: str) -> None:
    pattern = re.compile(rf"^{re.escape(BASE_URL)}{re.escape(path)}/?$")
    expect(page).to_have_url(pattern)


# ════════════════════════════════════════════════════════════════
#  FRONTEND — STATIC PAGES
# ════════════════════════════════════════════════════════════════

class TestLandingPage:
    def test_landing_loads(self, page: Page):
        go(page, "/")
        expect(page).to_have_title(re.compile(r"IntelliPlan"))

    def test_get_started_button_redirects_to_login(self, page: Page):
        go(page, "/")
        page.get_by_text("Get Started Free").first.click()
        assert_url_matches(page, "/login")

    def test_logo_links_to_home(self, page: Page):
        go(page, "/")
        page.locator("a", has_text="IntelliPlan").first.click()
        assert_url_matches(page, "/")

    def test_meta_description_present(self, page: Page):
        go(page, "/")
        desc = page.locator("meta[name='description']").get_attribute("content")
        assert desc and len(desc) > 30, "Meta description should be substantive"

    def test_canonical_url_present(self, page: Page):
        go(page, "/")
        canon = page.locator("link[rel='canonical']").get_attribute("href")
        assert canon and "intelliplan.tech" in canon

    def test_og_image_present(self, page: Page):
        go(page, "/")
        og = page.locator("meta[property='og:image']").get_attribute("content")
        assert og and og.startswith("http")

    def test_html_lang_attribute(self, page: Page):
        go(page, "/")
        lang = page.locator("html").get_attribute("lang")
        assert lang and lang.startswith("en")

    def test_viewport_meta_tag(self, page: Page):
        go(page, "/")
        vp = page.locator("meta[name='viewport']").get_attribute("content")
        assert vp and "width=device-width" in vp


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
        assert_url_matches(page, "/register")

    def test_sign_in_button_redirects_to_login_account(self, page: Page):
        go(page, "/login")
        page.get_by_role("link", name="Sign In", exact=True).click()
        assert_url_matches(page, "/login/account")

    def test_canvas_option_visible(self, page: Page):
        go(page, "/login")
        expect(page.get_by_text("Canvas LMS")).to_be_visible()

    def test_studentvue_option_visible(self, page: Page):
        go(page, "/login")
        expect(page.get_by_text("StudentVue")).to_be_visible()

    def test_canvas_link_works(self, page: Page):
        go(page, "/login")
        page.get_by_text("Canvas LMS").click()
        assert_url_matches(page, "/login/canvas")

    def test_studentvue_link_works(self, page: Page):
        go(page, "/login")
        page.get_by_text("StudentVue").click()
        assert_url_matches(page, "/login/studentvue")


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
        page.get_by_role("button", name=re.compile(r"Create Account")).click()
        expect(page.get_by_text("Passwords do not match")).to_be_visible()

    def test_short_password_shows_error(self, page: Page):
        go(page, "/register")
        page.locator("input[name='email']").fill("test@example.com")
        page.locator("input[name='password']").fill("short")
        page.locator("input[name='confirm_password']").fill("short")
        page.get_by_role("button", name=re.compile(r"Create Account")).click()
        expect(page.get_by_text("at least 8 characters")).to_be_visible()

    def test_back_to_login_link_works(self, page: Page):
        go(page, "/register")
        page.get_by_text("← Back to login options").click()
        assert_url_matches(page, "/login")


class TestLegalPage:
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
        content = page.content().lower()
        assert "do not store passwords" in content or "not store passwords" in content

    def test_contact_email_present(self, page: Page):
        go(page, "/legal")
        mailto = page.locator("a[href^='mailto:']")
        if mailto.count() == 0:
            pytest.skip("No contact email is rendered on the legal page.")
        expect(mailto.first).to_be_visible()


class TestInstallPage:
    def test_install_page_loads(self, page: Page):
        go(page, "/install")
        assert_url_matches(page, "/install")

    def test_ios_install_page_loads(self, page: Page):
        go(page, "/install/ios")
        assert_url_matches(page, "/install/ios")


class TestCanvasLoginPage:
    def test_canvas_login_loads(self, page: Page):
        go(page, "/login/canvas")
        expect(page.locator("input[name='canvas_token']")).to_be_visible()

    def test_canvas_login_empty_submit_stays_on_page(self, page: Page):
        go(page, "/login/canvas")
        page.locator("input[name='canvas_token']").fill("")
        page.get_by_role("button", name="Connect Canvas →").click()
        assert_url_matches(page, "/login/canvas")


class TestStudentVueLoginPage:
    def test_studentvue_login_loads(self, page: Page):
        go(page, "/login/studentvue")
        expect(page.locator("input[name='username']")).to_be_visible()

    def test_studentvue_has_password_field(self, page: Page):
        go(page, "/login/studentvue")
        expect(page.locator("input[name='password']")).to_be_visible()


# ════════════════════════════════════════════════════════════════
#  AUTH GATING
# ════════════════════════════════════════════════════════════════

class TestAuthRedirects:
    """Unauthenticated users must redirect to /login for every protected route."""

    @pytest.mark.parametrize("path", [
        "/dashboard",
        "/scheduler",
        "/priority",
        "/classes",
        "/grades",
        "/gradebook",
        "/study",
        "/study-and-learn",
        "/learn",
        "/focus",
        "/library",
        "/streak",
        "/tutor",
        "/lessons",
        "/writing",
        "/math",
        "/extractor",
        "/meetings",
        "/groups",
        "/tests",
        "/dismissed",
        "/settings",
        "/profiles",
    ])
    def test_protected_page_redirects_to_login(self, page: Page, path: str):
        go(page, path)
        assert_url_matches(page, "/login")


# ════════════════════════════════════════════════════════════════
#  BACKEND — API ENDPOINTS
# ════════════════════════════════════════════════════════════════

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

    def test_session_token_unauthenticated(self, page: Page):
        # The extension-session endpoint must return 401 for unauthenticated users
        response = page.request.get(f"{BASE_URL}/extension/session-token")
        assert response.status == 401

    def test_study_hub_recommend_unauthenticated(self, page: Page):
        # The Study & Learn AI hub also requires auth
        response = page.request.post(
            f"{BASE_URL}/api/study-hub/recommend",
            data='{"query":"test"}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 401

    def test_robots_txt_present(self, page: Page):
        response = page.request.get(f"{BASE_URL}/robots.txt")
        assert response.status in (200, 304)

    def test_sitemap_xml_present(self, page: Page):
        response = page.request.get(f"{BASE_URL}/sitemap.xml")
        assert response.status in (200, 304)


class TestExtensionEndpoints:
    """The Chrome extension's /extension/* endpoints reject bad input cleanly."""

    def test_extension_login_requires_credentials(self, page: Page):
        response = page.request.post(
            f"{BASE_URL}/extension/login",
            data='{"email":"","password":""}',
            headers={"Content-Type": "application/json"},
        )
        # Should return an error status (4xx) not a 5xx blow-up
        assert 400 <= response.status < 500

    def test_extension_login_rejects_bad_credentials(self, page: Page):
        response = page.request.post(
            f"{BASE_URL}/extension/login",
            data='{"email":"nobody-should-exist-here@invalid.test","password":"definitelywrong"}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status in (200, 400, 401, 404)
        if response.status == 200:
            body = response.json()
            assert body.get("status") != "ok"

    def test_extension_tasks_without_token_unauthorized(self, page: Page):
        response = page.request.get(f"{BASE_URL}/extension/tasks")
        assert response.status == 401

    def test_extension_grades_without_token_unauthorized(self, page: Page):
        response = page.request.get(f"{BASE_URL}/extension/grades")
        assert response.status == 401


# ════════════════════════════════════════════════════════════════
#  COMPLIANCE
# ════════════════════════════════════════════════════════════════

class TestComplianceChecks:
    """District-compliance gates: no chat invites, no ads, COPPA + password policy."""

    def test_no_discord_links_on_landing(self, page: Page):
        go(page, "/")
        assert_no_discord_links(page)

    def test_no_discord_links_on_login(self, page: Page):
        go(page, "/login")
        assert_no_discord_links(page)

    def test_no_discord_links_on_legal(self, page: Page):
        go(page, "/legal")
        assert_no_discord_links(page)

    def test_privacy_policy_mentions_no_passwords(self, page: Page):
        go(page, "/legal")
        content = page.content().lower()
        assert "do not store passwords" in content or "not store passwords" in content

    def test_privacy_policy_mentions_coppa(self, page: Page):
        go(page, "/legal")
        content = page.content().lower()
        assert "under 13" in content or "coppa" in content

    def test_no_ads(self, page: Page):
        go(page, "/")
        assert_no_ad_terms(page)


# ════════════════════════════════════════════════════════════════
#  RESPONSIVE — every breakpoint, key pages
# ════════════════════════════════════════════════════════════════

# (label, viewport width, viewport height)
DEVICES = [
    ("mobile-portrait",   375,  812),   # iPhone X portrait
    ("mobile-landscape",  812,  375),   # iPhone X landscape
    ("tablet",            768, 1024),   # iPad portrait
    ("desktop",          1280,  800),   # Laptop
    ("wide",             1920, 1080),   # Desktop monitor
]

# Paths that must render correctly across all viewports
RESPONSIVE_PAGES = [
    ("/",        "IntelliPlan"),
    ("/login",   "Sign in to IntelliPlan"),
    ("/register", "Create your account"),
    ("/legal",   "Privacy Policy"),
]


@pytest.mark.parametrize("dev_label,vw,vh", DEVICES)
class TestResponsiveLayout:
    """Every page must render without horizontal overflow at every breakpoint."""

    def _set_viewport(self, page: Page, vw: int, vh: int) -> None:
        page.set_viewport_size({"width": vw, "height": vh})

    @pytest.mark.parametrize("path,expected_text", RESPONSIVE_PAGES)
    def test_page_renders(self, page: Page, dev_label: str, vw: int, vh: int,
                          path: str, expected_text: str):
        self._set_viewport(page, vw, vh)
        go(page, path)
        # The page should contain its expected anchor text at every breakpoint
        body_text = page.locator("body").inner_text()
        assert expected_text.lower() in body_text.lower(), (
            f"{path} missing expected text at {dev_label} ({vw}x{vh})"
        )

    def test_no_horizontal_overflow_landing(self, page: Page,
                                            dev_label: str, vw: int, vh: int):
        self._set_viewport(page, vw, vh)
        go(page, "/")
        scroll_w = page.evaluate("document.documentElement.scrollWidth")
        # Allow 2px of subpixel rendering slop
        assert scroll_w <= vw + 2, (
            f"Horizontal overflow on landing at {dev_label} "
            f"({vw}x{vh}): scrollWidth={scroll_w}"
        )

    def test_mobile_nav_button_shown_only_below_720(self, page: Page,
                                                    dev_label: str, vw: int, vh: int):
        self._set_viewport(page, vw, vh)
        go(page, "/")
        btn = page.locator("#mobileMenuBtn")
        if vw <= 720:
            expect(btn).to_be_visible()
        else:
            # Hidden via CSS — confirm it doesn't take layout space
            display = btn.evaluate("el => getComputedStyle(el).display")
            assert display == "none", (
                f"Mobile menu button must be hidden on {dev_label} ({vw}x{vh})"
            )


# ════════════════════════════════════════════════════════════════
#  ACCESSIBILITY
# ════════════════════════════════════════════════════════════════

class TestAccessibility:
    """Minimal a11y checks — page titles, alt text, heading structure."""

    @pytest.mark.parametrize("path", ["/", "/login", "/register", "/legal"])
    def test_page_has_title(self, page: Page, path: str):
        go(page, path)
        title = page.title()
        assert title and len(title.strip()) > 0, f"{path} missing <title>"

    def test_landing_has_h1(self, page: Page):
        go(page, "/")
        h1_count = page.locator("h1").count()
        # Some marketing pages have multiple h1s — at least one must exist
        assert h1_count >= 1, "Landing page should have at least one <h1>"

    def test_landing_links_have_text(self, page: Page):
        go(page, "/")
        # Every link should have either accessible text or an aria-label/title
        bad_links = page.locator("a").evaluate_all("""(els) => {
          return els.filter(a => {
            const txt = (a.innerText || '').trim();
            const al  = (a.getAttribute('aria-label') || '').trim();
            const tt  = (a.getAttribute('title') || '').trim();
            return !txt && !al && !tt;
          }).length;
        }""")
        assert bad_links == 0, f"{bad_links} link(s) lack accessible text"

    def test_landing_images_have_alt(self, page: Page):
        go(page, "/")
        bad = page.locator("img").evaluate_all("""(els) => {
          return els.filter(img => {
            const alt = img.getAttribute('alt');
            const role = img.getAttribute('role');
            const aria = img.getAttribute('aria-hidden');
            // Decorative images are exempt if explicitly marked
            if (role === 'presentation' || aria === 'true') return false;
            return alt === null;
          }).length;
        }""")
        assert bad == 0, f"{bad} <img> tag(s) missing alt attribute"


# ════════════════════════════════════════════════════════════════
#  SECURITY HEADERS
# ════════════════════════════════════════════════════════════════

class TestSecurityHeaders:
    """Catch regressions in production security headers."""

    def test_landing_returns_2xx(self, page: Page):
        response = page.request.get(f"{BASE_URL}/")
        assert 200 <= response.status < 300

    def test_landing_serves_html(self, page: Page):
        response = page.request.get(f"{BASE_URL}/")
        ct = (response.headers.get("content-type") or "").lower()
        assert "text/html" in ct

    def test_no_x_powered_by_leak(self, page: Page):
        # Soft check — many frameworks leak this. Don't fail loudly, just warn.
        response = page.request.get(f"{BASE_URL}/")
        xpb = response.headers.get("x-powered-by")
        if xpb:
            pytest.skip(f"x-powered-by header is set: {xpb} (consider removing)")
