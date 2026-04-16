const BASE_URL = "https://intelliplan.up.railway.app";

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