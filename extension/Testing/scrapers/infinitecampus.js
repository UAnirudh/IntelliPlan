// Infinite Campus (Campus Portal) scraper.
//
// Hosts: *.infinitecampus.com, *.infinitecampus.org. Districts host their own
// Campus instance; the URL paths look like /campus/portal/.../course-information.
// First-pass selectors; expect to iterate once tested on a live install.

(function () {
  const SC = window.IntelliPlanScrapers;
  if (!SC) return;
  const U = SC.utils;

  function detect(host) {
    return /infinitecampus\.(com|org)$/.test(host) || /\.infinitecampus\.(com|org)$/.test(host);
  }

  function scrapeGrades() {
    const out = [];
    // Campus shows a list of course cards on the dashboard. We pick out
    // the elements that look like course summaries.
    for (const card of U.qsa(document, "[class*='course'], [class*='Course']")) {
      const heading = U.qs(card, "h2, h3, [class*='title'], [class*='name']");
      const courseName = U.text(heading);
      if (!courseName || courseName.length > 120) continue;
      let pct = null, letter = "", teacher = "";
      for (const txt of U.qsa(card, "*")) {
        const t = U.text(txt);
        if (!pct && /\d+(\.\d+)?\s*%/.test(t)) pct = U.parsePercent(t);
        if (!letter && /^[A-F][+-]?$/.test(t.trim())) letter = t.trim();
        if (!teacher && /teacher|instructor/i.test(t) && t.length < 80) {
          teacher = t.replace(/teacher:?|instructor:?/gi, "").trim();
        }
      }
      if (pct == null && !letter) continue;
      out.push({ course: courseName, percentage: pct, letter, teacher, period: "" });
    }
    return out;
  }

  function scrapeAssignments() {
    const assignments = [];
    // Assignment rows often live in tables under a 'Recent Assignments' header
    // or in the Assignment Center view.
    for (const row of U.qsa(document, "table tr, [class*='assignment-row']")) {
      const cells = U.qsa(row, "td, [class*='col']");
      if (cells.length < 3) continue;
      let due = "", title = "", course = "";
      for (const c of cells) {
        const t = U.text(c);
        if (!due) due = U.isoDate(t);
        if (!title && t.length > 5 && t.length < 200 && !/^\d/.test(t) && !/score|points/i.test(t)) title = t;
        if (!course && /[A-Z][a-z]+\s+[0-9]/.test(t)) course = t;
      }
      if (!title || !due) continue;
      assignments.push({
        title, course: course || "Infinite Campus class", due_date: due,
        priority: U.inferPriority(due),
        estimated_time: U.inferTime(title),
        notes: "",
        external_id: `ic:${title}:${due}`.slice(0, 128),
      });
    }
    return assignments;
  }

  async function scrape() {
    const grades = scrapeGrades();
    const assignments = scrapeAssignments();
    return { lms: "infinitecampus", label: "Infinite Campus", assignments, grades };
  }

  SC.register("infinitecampus", { detect, scrape });
})();
