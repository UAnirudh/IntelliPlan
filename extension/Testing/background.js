const BASE_URL = "https://intelliplan.tech";

chrome.alarms.create("checkDue", { periodInMinutes: 30 });

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "checkDue") await checkDueAssignments();
});

async function checkDueAssignments() {
  try {
    const stored = await chrome.storage.local.get(["authToken"]);
    const token = stored.authToken;
    if (!token) return;

    const res = await fetch(BASE_URL + "/extension/tasks", {
      headers: { "X-Extension-Token": token }
    });
    if (!res.ok) return;
    const data = await res.json();

    const overdue = data.overdue?.length || 0;
    const today = data.today?.length || 0;
    const total = overdue + today;

    if (overdue > 0) {
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icons/icon-128.png",
        title: "IntelliPlan — Overdue Work",
        message: `You have ${overdue} overdue assignment${overdue > 1 ? "s" : ""} that need attention.`,
        priority: 2
      });
    } else if (today > 0) {
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icons/icon-128.png",
        title: "IntelliPlan — Due Today",
        message: `${today} assignment${today > 1 ? "s" : ""} due today. Stay on track!`,
        priority: 1
      });
    }

    chrome.action.setBadgeText({ text: total > 0 ? String(total) : "" });
    chrome.action.setBadgeBackgroundColor({ color: overdue > 0 ? "#ef4444" : "#3b82f6" });
  } catch (e) {
    console.log("Background check failed:", e);
  }
}

chrome.runtime.onStartup.addListener(checkDueAssignments);
chrome.runtime.onInstalled.addListener(() => {
  checkDueAssignments();
  chrome.alarms.create("checkDue", { periodInMinutes: 30 });
});

chrome.notifications.onClicked.addListener(() => {
  chrome.tabs.create({ url: BASE_URL + "/dashboard" });
});

// ── Unsupported-LMS sync ─────────────────────────────────────
// Students whose school runs PowerSchool, Aeries, Infinite Campus, Skyward
// or eSchoolPlus have no API to connect to: those are district-managed and
// issue no developer keys. The content scripts scrape the page they are
// already signed in to and post the result here.
//
// Per-host throttle so a student who lives on their gradebook tab does not
// re-post it on every page view. The floating button in content.js forces a
// sync regardless.
const SYNC_INTERVAL_MS = 4 * 60 * 60 * 1000;
const LAST_SYNC_KEY = "intelliplan_last_sync_by_host";

async function _getLastSyncMap() {
  const obj = await chrome.storage.local.get([LAST_SYNC_KEY]);
  return obj[LAST_SYNC_KEY] || {};
}

async function _setLastSyncMap(map) {
  return chrome.storage.local.set({ [LAST_SYNC_KEY]: map });
}

async function pushScrapedData(payload) {
  // Same key the popup writes on sign-in. The older build of this code read
  // "intelliplan_token", which nothing in this extension has ever written,
  // so every sync went out unauthenticated and came back 401.
  const stored = await chrome.storage.local.get(["authToken"]);
  const token = stored.authToken;
  if (!token) return false;

  const useSmartPaste = !!(payload && payload._useSmartPaste);
  const endpoint = useSmartPaste ? "/api/import/smart_paste" : "/api/import/scraper";
  const body = useSmartPaste
    ? { text: payload._pastedText || "", hint: payload.label || "" }
    : {
        lms: payload.lms || "other",
        label: payload.label || "",
        assignments: payload.assignments || [],
        grades: payload.grades || [],
      };
  try {
    const res = await fetch(BASE_URL + endpoint, {
      method: "POST",
      credentials: "omit",
      headers: {
        "Content-Type": "application/json",
        "X-Extension-Token": token,
      },
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch (e) {
    console.warn("[IntelliPlan] sync push failed", e);
    return false;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || !msg.type) return;
  if (msg.type === "intelliplan_sync_check") {
    (async () => {
      try {
        const map = await _getLastSyncMap();
        const last = map[msg.host] || 0;
        sendResponse({ shouldSync: Date.now() - last > SYNC_INTERVAL_MS });
      } catch (_e) { sendResponse({ shouldSync: false }); }
    })();
    return true;
  }
  if (msg.type === "intelliplan_sync_push") {
    (async () => {
      try {
        const ok = await pushScrapedData(msg.payload);
        if (ok) {
          const map = await _getLastSyncMap();
          map[msg.host] = Date.now();
          await _setLastSyncMap(map);
        }
        sendResponse({ ok });
      } catch (_e) {
        sendResponse({ ok: false });
      }
    })();
    return true;
  }
});
