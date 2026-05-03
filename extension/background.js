const BASE_URL = "https://intelli-plan.up.railway.app";

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
