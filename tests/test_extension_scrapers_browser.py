"""The district-LMS scrapers, run in a real browser.

These are DOM scrapers: they depend on `location.host` to decide whether they
apply, and on the page's real table structure to find anything. Nothing about
them can be checked from Python, and nothing did check them -- which is how two
bugs survived that made the output actively harmful rather than merely empty:

  * `isoDate` fell through to `new Date(text)`, which reads "96%" as the year
    1996. Every row of a gradebook therefore looked like an assignment with a
    due date, so a Skyward or eSchoolPlus student got their course list
    injected into their task list as work due in 1996.

  * The title was taken as the first cell longer than five characters, which
    on an assignment row is the due date -- so assignments arrived named after
    their own due date.

Each test drives Chromium against a page shaped like the real portal, with the
district domain intercepted so `location.host` is genuinely theirs.

Skipped, not failed, where Playwright or Chromium is unavailable: this is a
browser test living in a suite that otherwise needs no browser.
"""

import pathlib

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="playwright not installed")

SCRAPERS = pathlib.Path(__file__).resolve().parent.parent / "extension" / "Testing" / "scrapers"

CHROMIUM_CANDIDATES = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
]


def _chromium_path():
    for p in CHROMIUM_CANDIDATES:
        if pathlib.Path(p).exists():
            return p
    for parent in pathlib.Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"):
        return str(parent)
    return None


pytestmark = pytest.mark.skipif(
    _chromium_path() is None or not SCRAPERS.is_dir(),
    reason="no Chromium build available for browser tests")


GRADEBOOK_AND_ASSIGNMENTS = """<html><body>
<table>
  <tr><th>Course</th><th>Teacher</th><th>Grade</th><th>Percent</th></tr>
  <tr><td>US Government</td><td>Alvarez</td><td>A</td><td>96%</td></tr>
  <tr><td>Physics</td><td>Okafor</td><td>B</td><td>85.5%</td></tr>
</table>
<table>
  <tr><th>Due</th><th>Assignment</th><th>Course</th><th>Points</th></tr>
  <tr><td>10/06/2026</td><td>Constitution quiz</td><td>US Government</td><td>30</td></tr>
  <tr><td>10/13/2026</td><td>Projectile motion lab</td><td>Physics</td><td>40</td></tr>
</table></body></html>"""


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        b = p.chromium.launch(executable_path=_chromium_path())
        yield b
        b.close()


def _scrape(browser, host, url, html, modules):
    ctx = browser.new_context()
    page = ctx.new_page()
    ctx.route(f"https://{host}/**",
              lambda route, request: route.fulfill(
                  status=200, content_type="text/html", body=html))
    page.goto(url, wait_until="domcontentloaded")
    page.add_script_tag(content=(SCRAPERS / "router.js").read_text())
    for m in modules:
        page.add_script_tag(content=(SCRAPERS / m).read_text())
    picked = page.evaluate(
        "() => { const p = window.IntelliPlanScrapers.pickForLocation();"
        " return p ? p.id : null; }")
    data = page.evaluate(
        "async () => await window.IntelliPlanScrapers.scrapeCurrent()")
    ctx.close()
    return picked, data


# ── The date parser, which is what made the output harmful ──

@pytest.mark.parametrize("text,expected", [
    ("2026-10-02", "2026-10-02"),
    ("10/02/2026", "2026-10-02"),
    ("Jun 12, 2026", "2026-06-12"),
    # The regression: none of these are dates, and Date() used to take them.
    ("96%", ""),
    ("88.5%", ""),
    ("A", ""),
    ("30", ""),
    ("", ""),
    ("US Government", ""),
])
def test_only_real_dates_parse_as_dates(browser, text, expected):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto("about:blank")
    page.add_script_tag(content=(SCRAPERS / "router.js").read_text())
    got = page.evaluate(
        "t => window.IntelliPlanScrapers.utils.isoDate(t)", text)
    ctx.close()
    assert got == expected, f"isoDate({text!r}) -> {got!r}"


# ── Skyward / eSchoolPlus: gradebook rows must not become assignments ──

@pytest.mark.parametrize("lms,host,url,module", [
    ("skyward", "usd1.skyward.com",
     "https://usd1.skyward.com/scripts/gradebook", "skyward.js"),
    ("eschoolplus", "hac.eschoolplus.com",
     "https://hac.eschoolplus.com/HomeAccess/Assignments.aspx", "eschoolplus.js"),
])
def test_a_gradebook_row_is_not_mistaken_for_an_assignment(browser, lms, host, url, module):
    picked, data = _scrape(browser, host, url, GRADEBOOK_AND_ASSIGNMENTS, [module])
    assert picked == lms
    titles = [a["title"] for a in data["assignments"]]
    assert "US Government" not in titles, f"course leaked in as work: {titles}"
    assert "Physics" not in titles, f"course leaked in as work: {titles}"
    assert "Constitution quiz" in titles


@pytest.mark.parametrize("lms,host,url,module", [
    ("skyward", "usd1.skyward.com",
     "https://usd1.skyward.com/scripts/gradebook", "skyward.js"),
    ("eschoolplus", "hac.eschoolplus.com",
     "https://hac.eschoolplus.com/HomeAccess/Assignments.aspx", "eschoolplus.js"),
])
def test_no_assignment_is_dated_in_the_nineties(browser, lms, host, url, module):
    """"96%" read as the year 1996 is what put this date in the task list."""
    _, data = _scrape(browser, host, url, GRADEBOOK_AND_ASSIGNMENTS, [module])
    dates = [a["due_date"] for a in data["assignments"]]
    assert all(d >= "2020" for d in dates), dates


@pytest.mark.parametrize("lms,host,url,module", [
    ("skyward", "usd1.skyward.com",
     "https://usd1.skyward.com/scripts/gradebook", "skyward.js"),
    ("eschoolplus", "hac.eschoolplus.com",
     "https://hac.eschoolplus.com/HomeAccess/Assignments.aspx", "eschoolplus.js"),
])
def test_an_assignment_is_not_named_after_its_own_due_date(browser, lms, host, url, module):
    _, data = _scrape(browser, host, url, GRADEBOOK_AND_ASSIGNMENTS, [module])
    for a in data["assignments"]:
        assert "/" not in a["title"], f"title looks like a date: {a['title']!r}"
        assert a["title"] != a["due_date"]


def test_grades_are_still_found_on_the_same_page(browser):
    """The date fix must not cost us the gradebook it was tightening around."""
    _, data = _scrape(browser, "usd1.skyward.com",
                      "https://usd1.skyward.com/scripts/gradebook",
                      GRADEBOOK_AND_ASSIGNMENTS, ["skyward.js"])
    courses = {g["course"]: g["percentage"] for g in data["grades"]}
    assert courses.get("US Government") == 96
    assert courses.get("Physics") == 85.5


# ── Aeries: an assignment must not be filed under itself ──

AERIES_PAGE = """<html><body>
<table>
  <tr><th>Period</th><th>Course</th><th>Teacher</th><th>Grade</th><th>Percent</th></tr>
  <tr><td>1</td><td><a href="/Assignments.aspx?gb=1">World History</a></td>
      <td>Nguyen</td><td>A</td><td>95.2%</td></tr>
</table>
<table>
  <tr><th>Due Date</th><th>Assignment</th><th>Score</th><th>Points</th></tr>
  <tr><td>10/03/2026</td><td>Cold War essay outline</td><td>9</td><td>10</td></tr>
</table></body></html>"""


def test_an_assignment_is_not_filed_under_itself(browser):
    """The course pattern also matches most assignment names, so a single
    pass labelled "Cold War essay outline" as its own course."""
    _, data = _scrape(browser, "riverside.aeries.net",
                      "https://riverside.aeries.net/student/Assignments.aspx",
                      AERIES_PAGE, ["aeries.js"])
    for a in data["assignments"]:
        assert a["course"] != a["title"], a


# ── Detection stays narrow ──

def test_a_scraper_does_not_claim_an_unrelated_site(browser):
    """Content scripts only load on the manifest's hosts, but a scraper that
    claimed any page would scrape a student's unrelated tabs if that changed."""
    picked, _ = _scrape(browser, "example.com", "https://example.com/",
                        GRADEBOOK_AND_ASSIGNMENTS,
                        ["skyward.js", "eschoolplus.js", "powerschool.js"])
    assert picked is None
