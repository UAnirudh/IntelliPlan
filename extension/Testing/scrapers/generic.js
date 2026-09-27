// Generic LMS scraper — last-resort fallback.
//
// When the page hostname doesn't match any of the specific LMS scrapers but
// looks like a school portal (has the word "gradebook" / "assignments" /
// "course" in the URL or title), we collect visible tables and ship them to
// the smart-paste endpoint instead of the structured /api/import/scraper
// endpoint. The server-side AI then figures out what the data means.
//
// This is the "anything else" safety net that lets the user opt into sync
// even on a portal we've never seen.

(function () {
  const SC = window.IntelliPlanScrapers;
  if (!SC) return;
  const U = SC.utils;

  function detect(host) {
    const url = (location.pathname + location.search + " " + document.title).toLowerCase();
    return /gradebook|assignments?|portal|grades|courses/.test(url);
  }

  function collectVisibleText() {
    const tables = U.qsa(document, "table");
    if (!tables.length) return "";
    let buf = [];
    for (const t of tables.slice(0, 6)) {
      const lines = [];
      for (const row of U.qsa(t, "tr")) {
        const cells = U.qsa(row, "th, td").map((c) => U.text(c).replace(/\s+/g, " "));
        if (cells.length) lines.push(cells.join("\t"));
      }
      if (lines.length) buf.push(lines.join("\n"));
    }
    return buf.join("\n\n").slice(0, 16000);
  }

  async function scrape() {
    const text = collectVisibleText();
    if (!text.trim()) return null;
    // We signal to content.js that this should go via /api/import/smart_paste
    // instead of the structured /api/import/scraper endpoint.
    return {
      lms: "generic",
      label: document.title.slice(0, 60) || "Generic portal",
      _useSmartPaste: true,
      _pastedText: text,
      assignments: [],
      grades: [],
    };
  }

  SC.register("generic", { detect, scrape });
})();
