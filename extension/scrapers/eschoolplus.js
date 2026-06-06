// eSchoolPlus Home Access Center scraper.
// Hosts: *.eschoolplus.com (and district-hosted variants share the HAC UI).

(function () {
  const SC = window.IntelliPlanScrapers;
  if (!SC) return;
  const U = SC.utils;

  function detect(host) {
    return /eschoolplus\.com$/.test(host) || /\.eschoolplus\.com$/.test(host);
  }

  function scrapeGrades() {
    const out = [];
    for (const row of U.qsa(document, "table tr")) {
      const cells = U.qsa(row, "td");
      if (cells.length < 3) continue;
      const courseName = U.text(cells[0]) || U.text(cells[1]);
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
    const out = [];
    for (const row of U.qsa(document, "table tr")) {
      const cells = U.qsa(row, "td");
      if (cells.length < 4) continue;
      let due = "", title = "";
      for (const c of cells) {
        const t = U.text(c);
        if (!due) due = U.isoDate(t);
        if (!title && t.length > 5 && t.length < 200) title = t;
      }
      if (!title || !due) continue;
      out.push({
        title, course: "eSchoolPlus class", due_date: due,
        priority: U.inferPriority(due),
        estimated_time: U.inferTime(title),
        notes: "",
        external_id: `esp:${title}:${due}`.slice(0, 128),
      });
    }
    return out;
  }

  async function scrape() {
    return {
      lms: "eschoolplus", label: "eSchoolPlus",
      assignments: scrapeAssignments(),
      grades: scrapeGrades(),
    };
  }

  SC.register("eschoolplus", { detect, scrape });
})();
