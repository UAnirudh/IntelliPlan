// Runs on Canvas and StudentVue pages
const BASE_URL = "https://intelliplan.tech";

function addIntelliPlanButton() {
  // Don't add twice
  if (document.getElementById("intelliplan-btn")) return;

  const btn = document.createElement("a");
  btn.id = "intelliplan-btn";
  btn.href = BASE_URL + "/dashboard";
  btn.target = "_blank";
  btn.style.cssText = `
    position: fixed;
    bottom: 24px;
    right: 24px;
    z-index: 99999;
    background: #3b82f6;
    color: white;
    padding: 10px 16px;
    border-radius: 12px;
    font-family: -apple-system, sans-serif;
    font-size: 13px;
    font-weight: 600;
    text-decoration: none;
    display: flex;
    align-items: center;
    gap: 6px;
    box-shadow: 0 4px 20px rgba(59,130,246,0.4);
    transition: transform 0.2s, box-shadow 0.2s;
    cursor: pointer;
  `;
  btn.innerHTML = "📅 IntelliPlan";
  btn.onmouseover = () => {
    btn.style.transform = "translateY(-2px)";
    btn.style.boxShadow = "0 8px 28px rgba(59,130,246,0.5)";
  };
  btn.onmouseout = () => {
    btn.style.transform = "";
    btn.style.boxShadow = "0 4px 20px rgba(59,130,246,0.4)";
  };

  document.body.appendChild(btn);
}

// Wait for page to load
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", addIntelliPlanButton);
} else {
  addIntelliPlanButton();
}

// ── Unsupported-LMS auto-sync ──────────────────────────────────
// When this page matches a registered scraper (PowerSchool / Aeries /
// Infinite Campus / Skyward / eSchoolPlus / generic portal), we ask the
// background script whether enough time has elapsed since the last sync
// for this host. If so, we scrape and POST to IntelliPlan.

(async function ipMaybeSync() {
  try {
    if (!window.IntelliPlanScrapers) return;
    const picked = window.IntelliPlanScrapers.pickForLocation();
    if (!picked) return;
    // Ask background.js if a sync is due for this host.
    chrome.runtime.sendMessage(
      { type: "intelliplan_sync_check", host: location.host },
      async (resp) => {
        if (!resp || !resp.shouldSync) return;
        const data = await window.IntelliPlanScrapers.scrapeCurrent();
        if (!data) return;
        chrome.runtime.sendMessage({
          type: "intelliplan_sync_push",
          host: location.host,
          payload: data,
        });
      }
    );
  } catch (_e) { /* never crash the host page */ }
})();

// Surface a small button to manually trigger a sync, alongside the existing
// IntelliPlan button. Helpful for first-run + testing.
(function ipAddSyncButton() {
  if (document.getElementById("intelliplan-sync-btn")) return;
  if (!window.IntelliPlanScrapers || !window.IntelliPlanScrapers.pickForLocation()) return;
  const wait = () => {
    if (!document.body) { setTimeout(wait, 300); return; }
    const btn = document.createElement("button");
    btn.id = "intelliplan-sync-btn";
    btn.type = "button";
    btn.style.cssText = `
      position: fixed; bottom: 24px; right: 160px; z-index: 99999;
      background: #4f46e5; color: white; padding: 10px 14px;
      border-radius: 12px; font-family: -apple-system, sans-serif;
      font-size: 12px; font-weight: 600; border: 0; cursor: pointer;
      box-shadow: 0 4px 16px rgba(79,70,229,0.25);
    `;
    btn.textContent = "Sync to IntelliPlan";
    btn.onclick = async () => {
      btn.disabled = true; btn.textContent = "Syncing...";
      try {
        const data = await window.IntelliPlanScrapers.scrapeCurrent();
        if (!data) { btn.textContent = "Nothing found"; return; }
        const ok = await new Promise((resolve) => {
          chrome.runtime.sendMessage(
            { type: "intelliplan_sync_push", host: location.host, payload: data, force: true },
            (r) => resolve(r && r.ok),
          );
        });
        btn.textContent = ok ? "Synced ✓" : "Sync failed";
      } catch (_e) {
        btn.textContent = "Sync failed";
      }
      setTimeout(() => { btn.disabled = false; btn.textContent = "Sync to IntelliPlan"; }, 2400);
    };
    document.body.appendChild(btn);
  };
  wait();
})();
