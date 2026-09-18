// IntelliPlan extension — scraper router.
//
// Each scraper module attaches itself to window.IntelliPlanScrapers under
// a string key (lms id). The router inspects the current host and picks
// the matching scraper. If nothing matches, it falls back to the generic
// scraper which best-effort extracts visible tables on the page.
//
// Conventions every scraper must follow:
//   - Export `detect()`         returns true when the page matches.
//   - Export `scrape()`         returns Promise<{lms, label, assignments, grades}>.
//   - assignments[] entries:    {title, course, due_date, priority, estimated_time, notes, external_id}
//   - grades[] entries:         {course, percentage, letter, teacher, period}
//   - Never throw — return empty arrays on failure so the router can fall back.

(function () {
  if (window.IntelliPlanScrapers) return;
  window.IntelliPlanScrapers = {
    _registry: {},
    register(id, mod) {
      this._registry[id] = mod;
    },
    pickForLocation() {
      const host = (location.host || "").toLowerCase();
      for (const id of Object.keys(this._registry)) {
        const mod = this._registry[id];
        try {
          if (mod.detect && mod.detect(host, location)) return { id, mod };
        } catch (_) { /* keep trying */ }
      }
      // Generic falls back last, only on hosts that look like a school portal.
      const generic = this._registry["generic"];
      if (generic && generic.detect(host, location)) return { id: "generic", mod: generic };
      return null;
    },
    async scrapeCurrent() {
      const picked = this.pickForLocation();
      if (!picked) return null;
      try {
        const data = await picked.mod.scrape();
        if (!data) return null;
        data.lms = data.lms || picked.id;
        data.label = data.label || picked.id;
        data.assignments = Array.isArray(data.assignments) ? data.assignments : [];
        data.grades = Array.isArray(data.grades) ? data.grades : [];
        return data;
      } catch (_e) {
        console.warn("[IntelliPlan] scrape failed for", picked.id, _e);
        return null;
      }
    },
  };

  // ── Shared helpers every scraper can use ──────────────────────
  window.IntelliPlanScrapers.utils = {
    text(el) { return (el && el.textContent || "").trim(); },
    qs(root, sel) { try { return (root || document).querySelector(sel); } catch (_) { return null; } },
    qsa(root, sel) { try { return Array.from((root || document).querySelectorAll(sel)); } catch (_) { return []; } },
    // Parse "92.5%" → 92.5, "A-" → null. Returns null if not a percent.
    parsePercent(s) {
      if (s == null) return null;
      const m = String(s).match(/(-?\d+(?:\.\d+)?)\s*%?/);
      if (!m) return null;
      const n = parseFloat(m[1]);
      return isFinite(n) ? n : null;
    },
    // Normalize a date-ish string to YYYY-MM-DD. Returns "" if it can't.
    isoDate(s) {
      if (!s) return "";
      const t = String(s).trim();
      // Already ISO?
      const iso = t.match(/^(\d{4})-(\d{2})-(\d{2})/);
      if (iso) return `${iso[1]}-${iso[2]}-${iso[3]}`;
      // US-style MM/DD or MM/DD/YYYY
      const us = t.match(/^(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?/);
      if (us) {
        const yr = us[3] ? (us[3].length === 2 ? `20${us[3]}` : us[3]) : String(new Date().getFullYear());
        return `${yr}-${us[1].padStart(2, "0")}-${us[2].padStart(2, "0")}`;
      }
      // "Jun 12, 2026" / "June 12 2026"
      const d = new Date(t);
      if (!isNaN(d.getTime())) {
        const yr = d.getFullYear(), mo = String(d.getMonth() + 1).padStart(2, "0"), day = String(d.getDate()).padStart(2, "0");
        return `${yr}-${mo}-${day}`;
      }
      return "";
    },
    // Map a due date to a default priority. The server re-derives this, but
    // a reasonable default keeps the dashboard ordering correct pre-sync.
    inferPriority(isoDue) {
      if (!isoDue) return "Medium";
      const now = new Date();
      const due = new Date(isoDue + "T23:59:59");
      const daysAway = (due - now) / 86400000;
      if (daysAway < 3) return "High";
      if (daysAway < 14) return "Medium";
      return "Low";
    },
    // Rough time estimate from the title. Tunable but better than a flat 60.
    inferTime(title) {
      const t = (title || "").toLowerCase();
      if (/test|exam|final|midterm/.test(t)) return 90;
      if (/essay|paper|report|project/.test(t)) return 120;
      if (/quiz|reading/.test(t)) return 30;
      if (/lab|worksheet|problem set|homework|hw/.test(t)) return 60;
      return 45;
    },
  };
})();
