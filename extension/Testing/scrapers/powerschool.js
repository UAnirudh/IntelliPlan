// PowerSchool student portal scraper.
//
// Hosts: *.powerschool.com (and PowerSchool-Public-Portal-style sub-paths).
// What it reads:
//   - The Grades and Attendance landing page (guardian.html / classroomsearch).
//     Each row is one course: course name, teacher, current grade %, letter.
//   - The Assignments page (scores.html) for each course — but the portal
//     only exposes assignments-per-class via a click. To keep one-pass scraping
//     reliable, the scraper crawls href="scores.html?..." links and fetches them
//     in parallel via fetch() with credentials:"include" so we get the auth
//     cookie for free.
//
// This is the reference implementation. The other LMSes use the same structure.

(function () {
  const SC = window.IntelliPlanScrapers;
  if (!SC) return;
  const U = SC.utils;

  function detect(host) {
    return /\.powerschool\.com$/.test(host) || /powerschool\.com$/.test(host);
  }

  // ── Grades page ─────────────────────────────────────────────
  // PowerSchool's gradebook rows live in #scrollContainer table tr or
  // similar — selectors vary between district skins, so we tolerate
  // several layouts.
  async function scrapeGrades() {
    const out = [];
    const rows = U.qsa(document, "table tr").filter((r) => {
      const cells = U.qsa(r, "td");
      return cells.length >= 3 && U.qsa(r, "th").length === 0;
    });
    for (const row of rows) {
      const cells = U.qsa(row, "td");
      // Heuristic: a course row has a course name link plus at least one cell
      // that looks like a percentage or letter grade.
      const link = U.qs(row, "a[href*='scores.html'], a[href*='assignments']");
      const courseName = link ? U.text(link) : U.text(cells[0]);
      if (!courseName || courseName.length > 120) continue;
      // Teacher is often the next link or the cell after the course.
      let teacher = "";
      const teacherLink = U.qs(row, "a[href*='mailto'], a[href*='email']");
      if (teacherLink) teacher = U.text(teacherLink);
      // Find a percent and a letter anywhere in the row.
      let pct = null, letter = "";
      for (const c of cells) {
        const txt = U.text(c);
        if (!pct && /\d+(\.\d+)?\s*%/.test(txt)) pct = U.parsePercent(txt);
        if (!letter && /^[A-F][+-]?$/.test(txt.trim())) letter = txt.trim();
      }
      if (pct == null && !letter) continue;
      out.push({
        course: courseName,
        percentage: pct,
        letter: letter,
        teacher: teacher,
        period: "",
      });
    }
    return out;
  }

  // ── Assignments per class ───────────────────────────────────
  async function scrapeAssignmentsFromGradesPage() {
    const assignments = [];
    // Each course row links to scores.html?frn=...
    const courseLinks = U.qsa(document, "a[href*='scores.html']");
    const seenUrls = new Set();
    // Limit to first 12 classes to keep the sync under a few seconds.
    for (const a of courseLinks.slice(0, 12)) {
      const url = a.href;
      if (!url || seenUrls.has(url)) continue;
      seenUrls.add(url);
      const courseName = U.text(a) || "Unknown course";
      try {
        const res = await fetch(url, { credentials: "include" });
        if (!res.ok) continue;
        const html = await res.text();
        const doc = new DOMParser().parseFromString(html, "text/html");
        // Assignment rows in scores.html — each <tr> with a date cell and
        // an assignment name. We pull date, name, score, total.
        for (const row of U.qsa(doc, "table tr")) {
          const cells = U.qsa(row, "td");
          if (cells.length < 4) continue;
          const dueText = U.text(cells[0]);
          const due = U.isoDate(dueText);
          // Skip header-ish rows that have no date.
          if (!due) continue;
          // Find the name cell — usually the longest text cell that isn't a date.
          let name = "";
          for (const c of cells.slice(1, 5)) {
            const t = U.text(c);
            if (t && t.length > name.length && !/\d{4}-\d{2}-\d{2}/.test(t)) name = t;
          }
          if (!name || name.length > 240) continue;
          assignments.push({
            title: name,
            course: courseName,
            due_date: due,
            priority: U.inferPriority(due),
            estimated_time: U.inferTime(name),
            notes: "",
            external_id: `ps:${url}:${name}:${due}`.slice(0, 128),
          });
        }
      } catch (_e) { /* keep going */ }
    }
    return assignments;
  }

  async function scrape() {
    const [grades, assignments] = await Promise.all([
      scrapeGrades().catch(() => []),
      scrapeAssignmentsFromGradesPage().catch(() => []),
    ]);
    let label = "PowerSchool";
    const districtMatch = (location.host.match(/^([^.]+)\.powerschool\.com$/) || [])[1];
    if (districtMatch) label = `PowerSchool (${districtMatch})`;
    return { lms: "powerschool", label, assignments, grades };
  }

  SC.register("powerschool", { detect, scrape });
})();
