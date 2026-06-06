const BASE_URL = "https://intelliplan.tech";

const STORAGE_KEYS = {
  token: "intelliplan_token"
};

function getStorage(keys) {
  return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
}

async function getToken() {
  const result = await getStorage([STORAGE_KEYS.token]);
  return result[STORAGE_KEYS.token] || null;
}

// Check for due assignments every 30 minutes
chrome.alarms.create("checkDue", { periodInMinutes: 30 });

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "checkDue") {
    await checkDueAssignments();
  }
});

async function checkDueAssignments() {
  try {
    const token = await getToken();
    if (!token) {
      chrome.action.setBadgeText({ text: "" });
      return;
    }

    const res = await fetch(BASE_URL + "/tasks/unified", {
      credentials: "omit",
      headers: {
        Authorization: `Bearer ${token}`
      }
    });
    if (!res.ok) return;
    const data = await res.json();

    const overdue = data.overdue?.length || 0;
    const today = data.today?.length || 0;

    if (overdue > 0) {
      chrome.notifications.create("overdue", {
        type: "basic",
        iconUrl: "icons/icon-128.png",
        title: "IntelliPlan — Overdue Work",
        message: `You have ${overdue} overdue assignment${overdue > 1 ? "s" : ""} that need attention.`,
        priority: 2
      });
    } else if (today > 0) {
      chrome.notifications.create("today", {
        type: "basic",
        iconUrl: "icons/icon-128.png",
        title: "IntelliPlan — Due Today",
        message: `${today} assignment${today > 1 ? "s" : ""} due today. Stay on track!`,
        priority: 1
      });
    }

    // Update badge
    const total = overdue + today;
    chrome.action.setBadgeText({ text: total > 0 ? String(total) : "" });
    chrome.action.setBadgeBackgroundColor({
      color: overdue > 0 ? "#ef4444" : "#3b82f6"
    });
  } catch (e) {
    console.log("Background check failed:", e);
  }
}

// Run on startup
chrome.runtime.onStartup.addListener(checkDueAssignments);
chrome.runtime.onInstalled.addListener(() => {
  checkDueAssignments();
  chrome.alarms.create("checkDue", { periodInMinutes: 30 });
});

// Open app on notification click
chrome.notifications.onClicked.addListener(() => {
  chrome.tabs.create({ url: BASE_URL + "/dashboard" });
});

// ── Unsupported-LMS auto-sync state ──────────────────────────
// Per-host throttle: by default we won't scrape the same host more than
// once every 4 hours. The user can force-sync any time from the floating
// button injected by content.js.
const SYNC_INTERVAL_MS = 4 * 60 * 60 * 1000;
const LAST_SYNC_KEY = "intelliplan_last_sync_by_host";

async function _getLastSyncMap() {
  const obj = await getStorage([LAST_SYNC_KEY]);
  return obj[LAST_SYNC_KEY] || {};
}

async function _setLastSyncMap(map) {
  return new Promise((res) => chrome.storage.local.set({ [LAST_SYNC_KEY]: map }, res));
}

async function pushScrapedData(payload) {
  const token = await getToken();
  // We try authenticated first (token), then fall back to cookie auth if
  // the user is logged in via the same browser. Cookie auth fails CORS
  // on cross-origin POSTs, so token is the primary path.
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
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch (_e) {
    console.warn("[IntelliPlan] sync push failed", _e);
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
        const due = Date.now() - last > SYNC_INTERVAL_MS;
        sendResponse({ shouldSync: due });
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
