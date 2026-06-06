// Skyward Student Access scraper.
// Skyward is heavily iframed and uses ASP.NET WebForms — the assignment grid
// is rendered inside `#sf_grid_<...>` containers. We grab the visible tables.

(function () {
  const SC = window.IntelliPlanScrapers;
  if (!SC) return;
  const U = SC.utils;

  function detect(host) {
    return /\.skyward\.com$/.test(host) || /skyward\.com$/.test(host);
  }

  function scrapeGrades() {
    const out = [];
    for (const row of U.qsa(document, "table tr")) {
      const cells = U.qsa(row, "td");
      if (cells.length < 4) continue;
      const courseCell = cells.find((c) => U.text(c).length > 4);
      const courseName = U.text(courseCell || cells[0]);
      if (!courseName || courseName.length > 120) continue;
      let pct = null, letter = "";
      for (const c of cells) {
        const t = U.text(c);
        if (!pct && /\d+(\.\d+)?\s*%/.test(t)) pct = U.parsePercent(t);
        if (!letter && /^[A-F][+-]?$/.test(t.trim())) letter = t.trim();
      }
      if (pct == null && !letter) continue;
      out.push({ course: courseName, percentage: pct, letter, teacher: "", period: "" });
    }
    return out;
  }

  function scrapeAssignments() {
    const assignments = [];
    for (const row of U.qsa(document, "table tr")) {
      const cells = U.qsa(row, "td");
      if (cells.length < 4) continue;
      let due = "", title = "", course = "";
      for (const c of cells) {
        const t = U.text(c);
        if (!due) due = U.isoDate(t);
        if (!title && t.length > 5 && t.length < 200) title = t;
      }
      if (!title || !due) continue;
      assignments.push({
        title, course: course || "Skyward class", due_date: due,
        priority: U.inferPriority(due),
        estimated_time: U.inferTime(title),
        notes: "",
        external_id: `sky:${title}:${due}`.slice(0, 128),
      });
    }
    return assignments;
  }

  async function scrape() {
    return {
      lms: "skyward", label: "Skyward",
      assignments: scrapeAssignments(),
      grades: scrapeGrades(),
    };
  }

  SC.register("skyward", { detect, scrape });
})();
