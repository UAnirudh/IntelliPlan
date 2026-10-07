// IntelliPlan extension: completion percentage for a tracked course.
//
// Runs on Khan Academy, Coursera and edX. It does nothing on a page unless
// that page belongs to a course the student added at intelliplan.tech/my-courses:
// the list of tracked pages is fetched first, and a page that is not on it
// is never read.
//
// What is read is one number: a completion percentage the site already
// shows the student. No page text, no answers, no history leaves the tab.
//
// The sites publish no API for this, so the percentage is found the way a
// person would find it: a progress bar that says how full it is, or the
// words "NN% complete". When neither is on the page nothing is sent.

(function () {
  if (window.__intelliplanCourses) return;
  window.__intelliplanCourses = true;

  const REPORT_EVERY_MS = 10 * 60 * 1000;
  const SETTLE_MS = 4000; // these sites draw their progress after load

  function matchKey(href) {
    try {
      const u = new URL(href);
      const host = u.hostname.toLowerCase().replace(/^www\./, "");
      return host + u.pathname.replace(/\/+$/, "").toLowerCase();
    } catch (_e) {
      return "";
    }
  }

  // Same rule as course_tracking.same_course on the server.
  function isTracked(tracked, here) {
    return tracked.some((t) => {
      if (!t || t.indexOf("/") === -1) return t === here;
      return here === t || here.indexOf(t + "/") === 0;
    });
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function readPercent() {
    // 1. A progress bar that states its own value.
    const bars = Array.from(document.querySelectorAll('[role="progressbar"][aria-valuenow]'));
    for (const bar of bars) {
      if (!visible(bar)) continue;
      const now = parseFloat(bar.getAttribute("aria-valuenow"));
      const max = parseFloat(bar.getAttribute("aria-valuemax") || "100");
      const label = (bar.getAttribute("aria-label") || bar.getAttribute("aria-valuetext") || "").toLowerCase();
      // Skip video scrubbers and upload meters: only bars about the course.
      if (/video|volume|seek|upload|loading/.test(label)) continue;
      if (isFinite(now) && isFinite(max) && max > 0) {
        const pct = (now / max) * 100;
        if (pct >= 0 && pct <= 100) return Math.round(pct * 10) / 10;
      }
    }
    // 2. The words the sites use next to their own bars.
    const text = (document.body && document.body.innerText || "").slice(0, 20000);
    const m = text.match(/(\d{1,3})\s?%\s*(?:complete|completed|mastered|course mastery|of course)/i);
    if (m) {
      const pct = parseInt(m[1], 10);
      if (pct >= 0 && pct <= 100) return pct;
    }
    return null;
  }

  function recentlyReported(key) {
    try {
      return Date.now() - Number(sessionStorage.getItem("ip_course_" + key) || 0) < REPORT_EVERY_MS;
    } catch (_e) {
      return false;
    }
  }

  function markReported(key) {
    try { sessionStorage.setItem("ip_course_" + key, String(Date.now())); } catch (_e) { /* private mode */ }
  }

  function run() {
    const here = matchKey(location.href);
    if (!here || recentlyReported(here)) return;
    chrome.runtime.sendMessage({ type: "intelliplan_course_pages" }, (res) => {
      if (chrome.runtime.lastError || !res || !Array.isArray(res.tracked)) return;
      if (!isTracked(res.tracked, here)) return; // not a tracked course: read nothing
      const percent = readPercent();
      if (percent === null) return;
      chrome.runtime.sendMessage(
        { type: "intelliplan_course_progress", url: location.origin + location.pathname, percent },
        () => { if (!chrome.runtime.lastError) markReported(here); }
      );
    });
  }

  setTimeout(run, SETTLE_MS);
})();
