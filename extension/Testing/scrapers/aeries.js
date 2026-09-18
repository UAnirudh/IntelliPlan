// Aeries SIS scraper.
//
// Hosts: *.aeries.net (per-district subdomains like riverside.aeries.net).
// Aeries' student portal exposes:
//   - /Student/Gradebook.aspx → current grades per class (course, %, letter)
//   - /Student/Assignments.aspx → assignment list (title, due date, course, score)
//
// Selectors are kept loose so they survive Aeries' periodic UI refreshes.
// First-pass implementation — likely needs tweaking once we test on a live
// district install. Returns empty arrays rather than throwing on mismatch.

(function () {
  const SC = window.IntelliPlanScrapers;
  if (!SC) return;
  const U = SC.utils;

  function detect(host) {
    return /\.aeries\.net$/.test(host) || /aeries\.net$/.test(host);
  }

  function scrapeGrades() {
    const out = [];
    // Aeries renders the gradebook summary as a table with course rows.
    // Each row has class period, course name, teacher, current %, letter.
    for (const row of U.qsa(document, "table tr")) {
      const cells = U.qsa(row, "td");
      if (cells.length < 4) continue;
      const courseCell = cells.find((c) => U.text(c).length > 4 && !/^\d/.test(U.text(c)));
      if (!courseCell) continue;
      const courseName = U.text(courseCell);
      if (courseName.length > 120) continue;
      let pct = null, letter = "", teacher = "", period = "";
      for (const c of cells) {
        const t = U.text(c);
        if (!pct && /\d+(\.\d+)?\s*%/.test(t)) pct = U.parsePercent(t);
        if (!letter && /^[A-F][+-]?$/.test(t.trim())) letter = t.trim();
        if (!period && /^\d{1,2}$/.test(t.trim())) period = t.trim();
        if (!teacher && /,\s+[A-Z]/.test(t) && t.length < 60 && t !== courseName) teacher = t;
      }
      if (pct == null && !letter) continue;
      out.push({ course: courseName, percentage: pct, letter, teacher, period });
    }
    return out;
  }

  async function scrapeAssignments() {
    const assignments = [];
    // Try the Assignments.aspx page if we're not already there.
    let html = document.documentElement.outerHTML;
    if (!/Assignments\.aspx/i.test(location.pathname)) {
      try {
        const url = `${location.origin}/Student/Assignments.aspx`;
        const r = await fetch(url, { credentials: "include" });
        if (r.ok) html = await r.text();
      } catch (_e) { /* keep current page */ }
    }
    const doc = new DOMParser().parseFromString(html, "text/html");
    for (const row of U.qsa(doc, "table tr")) {
      const cells = U.qsa(row, "td");
      if (cells.length < 4) continue;
      let due = "", title = "", course = "", notes = "";
      for (const c of cells) {
        const t = U.text(c);
        if (!due) due = U.isoDate(t);
        if (!title && t.length > 5 && t.length < 200 && !/^\d/.test(t)) title = t;
        if (!course && /^[A-Z][a-z]+\s+[A-Z0-9]/.test(t) && t.length < 80) course = t;
      }
      if (!title || !due) continue;
      assignments.push({
        title, course: course || "Aeries class", due_date: due,
        priority: U.inferPriority(due),
        estimated_time: U.inferTime(title),
        notes, external_id: `aeries:${title}:${due}`.slice(0, 128),
      });
    }
    return assignments;
  }

  async function scrape() {
    const [grades, assignments] = await Promise.all([
      Promise.resolve(scrapeGrades()).catch(() => []),
      scrapeAssignments().catch(() => []),
    ]);
    const districtMatch = (location.host.match(/^([^.]+)\.aeries\.net$/) || [])[1];
    const label = districtMatch ? `Aeries (${districtMatch})` : "Aeries";
    return { lms: "aeries", label, assignments, grades };
  }

  SC.register("aeries", { detect, scrape });
})();
