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

// ── Focus Shield: blocking that follows the plan ─────────────
// Blocks the student's distractor list ONLY while a planned study block or
// an Active session is running, and lifts it on its own when the block
// ends. The server says when that is (/extension/focus/current); the answer
// is cached so the current block keeps being enforced if the network drops.
//
// Nothing about browsing is ever sent anywhere. The only requests this
// makes are "what is my current block?", "I took a break" and "I'm done
// early".
//
// Enforcement is a declarativeNetRequest dynamic rule per domain, so it
// holds even while this service worker is asleep. The friendly block page
// is layered on top: when a tab lands on a blocked site, it is sent to
// blocked.html, which shows the task, the time left, and the way out.
const FS_STATE_KEY = "focusShieldState";      // last server answer + fetchedAt
const FS_LOCAL_KEY = "focusShieldLocal";      // offline breaks / releases / popup timer
const FS_RULE_BASE = 9000;                     // our dynamic rule ids: 9000..9099
const FS_RULE_MAX = 100;
const FS_POLL_ALARM = "focusShieldPoll";
const FS_EDGE_ALARM = "focusShieldEdge";
const FS_BREAK_MINUTES = 5;
const FS_DEFAULT_BLOCKLIST = [
  "youtube.com", "tiktok.com", "instagram.com", "reddit.com", "x.com",
  "twitter.com", "netflix.com", "discord.com", "roblox.com",
];

function fsTimezone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) { return ""; }
}

async function fsGet(key, fallback) {
  const obj = await chrome.storage.local.get([key]);
  return obj[key] ?? fallback;
}

async function fsSet(key, value) {
  return chrome.storage.local.set({ [key]: value });
}

async function fsFetch(path, method = "GET") {
  const token = (await chrome.storage.local.get(["authToken"])).authToken;
  if (!token) return { ok: false, status: 401, data: null };
  const sep = path.includes("?") ? "&" : "?";
  try {
    const res = await fetch(BASE_URL + path + sep + "tz=" + encodeURIComponent(fsTimezone()), {
      method,
      credentials: "omit",
      headers: { "Content-Type": "application/json", "X-Extension-Token": token },
      body: method === "POST" ? "{}" : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    return { ok: res.ok, status: res.status, data };
  } catch (_) {
    return { ok: false, status: 0, data: null };  // offline
  }
}

// The block happening now, judged against this device's clock from the
// cached answer -- which is what keeps enforcement right offline.
function fsCurrentBlock(state, local, now) {
  if (local.manual && Date.parse(local.manual.ends_at) > now) return local.manual;
  if (!state) return null;
  const candidates = [state.current, ...(state.upcoming || [])].filter(Boolean);
  return candidates.find(b => Date.parse(b.starts_at) <= now && now < Date.parse(b.ends_at)) || null;
}

function fsDecide(state, local, now = Date.now()) {
  const block = fsCurrentBlock(state, local, now);
  const enabled = state ? state.enabled !== false : !!local.manual;
  if (!enabled || !block) return { blocking: false, block: null };
  const serverKey = state && state.current && state.current.key;
  const onBreak =
    (local.breakUntil && local.breakKey === block.key && Date.parse(local.breakUntil) > now) ||
    (state && serverKey === block.key && state.break_until && Date.parse(state.break_until) > now);
  const released =
    (local.releasedKey === block.key) ||
    (state && serverKey === block.key && state.released);
  return { blocking: !onBreak && !released, block, onBreak: !!onBreak, released: !!released };
}

function fsBlocklist(state) {
  const list = (state && Array.isArray(state.blocklist)) ? state.blocklist : FS_DEFAULT_BLOCKLIST;
  return list.slice(0, FS_RULE_MAX);
}

async function fsApplyRules(blocking, domains) {
  const existing = await chrome.declarativeNetRequest.getDynamicRules();
  const ours = existing.map(r => r.id).filter(id => id >= FS_RULE_BASE && id < FS_RULE_BASE + FS_RULE_MAX);
  const addRules = blocking ? domains.map((domain, i) => ({
    id: FS_RULE_BASE + i,
    priority: 1,
    action: { type: "block" },
    condition: { requestDomains: [domain], resourceTypes: ["main_frame", "sub_frame"] },
  })) : [];
  await chrome.declarativeNetRequest.updateDynamicRules({ removeRuleIds: ours, addRules });
}

function fsHostMatches(url, domains) {
  let host = "";
  try { host = new URL(url).hostname.toLowerCase(); } catch (_) { return false; }
  return domains.some(d => host === d || host.endsWith("." + d));
}

function fsBlockedPage(url) {
  return chrome.runtime.getURL("blocked.html?u=" + encodeURIComponent(url));
}

async function fsRedirectOpenTabs(domains) {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (tab.id != null && tab.url && fsHostMatches(tab.url, domains)) {
      chrome.tabs.update(tab.id, { url: fsBlockedPage(tab.url) });
    }
  }
}

// Re-evaluate from cache and wake again at the next block edge, so a block
// starts and ends on time between polls and while offline.
async function fsEvaluate() {
  const state = await fsGet(FS_STATE_KEY, null);
  const local = await fsGet(FS_LOCAL_KEY, {});
  const now = Date.now();
  const decision = fsDecide(state, local, now);
  const domains = fsBlocklist(state);
  const wasBlocking = await fsGet("focusShieldBlocking", false);
  await fsApplyRules(decision.blocking, domains);
  await fsSet("focusShieldBlocking", decision.blocking);
  if (decision.blocking && !wasBlocking) await fsRedirectOpenTabs(domains);

  const edges = [];
  for (const b of [state && state.current, ...((state && state.upcoming) || []), local.manual]) {
    if (!b) continue;
    for (const t of [Date.parse(b.starts_at), Date.parse(b.ends_at)]) if (t > now) edges.push(t);
  }
  for (const t of [Date.parse(local.breakUntil || ""), Date.parse((state && state.break_until) || "")]) {
    if (t > now) edges.push(t);
  }
  if (edges.length) chrome.alarms.create(FS_EDGE_ALARM, { when: Math.min(...edges) + 1000 });
  return decision;
}

async function fsRefresh() {
  const res = await fsFetch("/extension/focus/current");
  if (res.ok && res.data && res.data.status === "ok") {
    const local = await fsGet(FS_LOCAL_KEY, {});
    // Offline overrides belong to one block; drop them once it is over.
    const key = res.data.current && res.data.current.key;
    if (local.breakKey && local.breakKey !== key) { delete local.breakKey; delete local.breakUntil; delete local.breaksUsed; }
    if (local.releasedKey && local.releasedKey !== key) delete local.releasedKey;
    await fsSet(FS_LOCAL_KEY, local);
    await fsSet(FS_STATE_KEY, { ...res.data, fetchedAt: Date.now() });
  } else if (res.status === 401) {
    await fsSet(FS_STATE_KEY, null);   // signed out: never block on a stale plan
  }
  return fsEvaluate();
}

function fsAllowed(state) {
  return state && Number.isInteger(state.breaks_per_block) ? state.breaks_per_block : 2;
}

function fsServerBreaksUsed(state, block) {
  return state && block && state.current && state.current.key === block.key ? (state.breaks_used || 0) : 0;
}

async function fsTakeBreak() {
  const state = await fsGet(FS_STATE_KEY, null);
  const local = await fsGet(FS_LOCAL_KEY, {});
  const decision = fsDecide(state, local);
  if (!decision.block) return { ok: false, message: "No study block is running." };
  if (decision.block.source !== "manual") {
    const res = await fsFetch("/extension/focus/break", "POST");
    if (res.ok && res.data && res.data.status === "ok") {
      await fsRefresh();
      return { ok: true, break_until: res.data.break_until, breaks_left: res.data.breaks_left };
    }
    if (res.status !== 0) return { ok: false, message: (res.data && res.data.message) || "No breaks left in this block." };
  }
  // Offline, or the popup's own timer: the same allowance, counted here.
  const allowed = fsAllowed(state);
  const serverUsed = fsServerBreaksUsed(state, decision.block);
  const used = local.breakKey === decision.block.key ? (local.breaksUsed || 0) : 0;
  if (used + serverUsed >= allowed) return { ok: false, message: "No breaks left in this block." };
  local.breakKey = decision.block.key;
  local.breaksUsed = used + 1;
  local.breakUntil = new Date(Date.now() + FS_BREAK_MINUTES * 60000).toISOString();
  await fsSet(FS_LOCAL_KEY, local);
  await fsEvaluate();
  return { ok: true, break_until: local.breakUntil, breaks_left: allowed - serverUsed - local.breaksUsed };
}

async function fsDoneEarly() {
  const state = await fsGet(FS_STATE_KEY, null);
  const local = await fsGet(FS_LOCAL_KEY, {});
  const decision = fsDecide(state, local);
  if (!decision.block) return { ok: true };
  if (decision.block.source === "manual") {
    delete local.manual;
  } else {
    local.releasedKey = decision.block.key;   // holds offline; the server is told too
    fsFetch("/extension/focus/done", "POST").then(r => { if (r.ok) fsRefresh(); });
  }
  await fsSet(FS_LOCAL_KEY, local);
  await fsEvaluate();
  return { ok: true };
}

async function fsStatus() {
  const state = await fsGet(FS_STATE_KEY, null);
  const local = await fsGet(FS_LOCAL_KEY, {});
  const decision = fsDecide(state, local);
  const localUsed = decision.block && local.breakKey === decision.block.key ? (local.breaksUsed || 0) : 0;
  return {
    ...decision,
    breaks_left: Math.max(0, fsAllowed(state) - fsServerBreaksUsed(state, decision.block) - localUsed),
    break_minutes: FS_BREAK_MINUTES,
    next: state ? state.next : null,
    offline_since: state && state.fetchedAt && Date.now() - state.fetchedAt > 3 * 60000 ? state.fetchedAt : null,
  };
}

chrome.alarms.create(FS_POLL_ALARM, { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FS_POLL_ALARM) fsRefresh();
  else if (alarm.name === FS_EDGE_ALARM) fsEvaluate();
});
chrome.runtime.onStartup.addListener(fsRefresh);
chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(FS_POLL_ALARM, { periodInMinutes: 1 });
  fsRefresh();
});

// A tab that lands on a blocked site gets the friendly page instead of
// Chrome's bare "blocked" error. The DNR rule is what enforces; this only
// changes what the student sees.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  const url = changeInfo.url || (changeInfo.status === "loading" ? tab.url : null);
  if (!url || url.startsWith(chrome.runtime.getURL(""))) return;
  if (!(await fsGet("focusShieldBlocking", false))) return;
  const state = await fsGet(FS_STATE_KEY, null);
  if (fsHostMatches(url, fsBlocklist(state))) chrome.tabs.update(tabId, { url: fsBlockedPage(url) });
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || typeof msg.type !== "string" || !msg.type.startsWith("focus_shield_")) return;
  (async () => {
    try {
      if (msg.type === "focus_shield_status") sendResponse(await fsStatus());
      else if (msg.type === "focus_shield_break") sendResponse(await fsTakeBreak());
      else if (msg.type === "focus_shield_done") sendResponse(await fsDoneEarly());
      else if (msg.type === "focus_shield_refresh") sendResponse(await fsRefresh());
      else if (msg.type === "focus_shield_local_start") {
        // The popup's own focus timer counts as a focus session too.
        const minutes = Math.max(1, Math.min(180, parseInt(msg.minutes, 10) || 25));
        const local = await fsGet(FS_LOCAL_KEY, {});
        const start = new Date();
        local.manual = {
          key: "manual-" + start.getTime(), source: "manual", title: "Focus session", course: "",
          starts_at: start.toISOString(), ends_at: new Date(start.getTime() + minutes * 60000).toISOString(),
        };
        await fsSet(FS_LOCAL_KEY, local);
        sendResponse(await fsEvaluate());
      } else if (msg.type === "focus_shield_local_stop") {
        const local = await fsGet(FS_LOCAL_KEY, {});
        delete local.manual;
        await fsSet(FS_LOCAL_KEY, local);
        sendResponse(await fsEvaluate());
      } else sendResponse(null);
    } catch (e) {
      sendResponse({ ok: false, message: String((e && e.message) || e) });
    }
  })();
  return true;
});

// ── Omnibox quick add: type "ip", space, then the task ──────────────
// "ip bio lab due fri 2h" ↵ adds it without opening anything. Same server
// parser as the popup and the web palette, so the date means the same thing
// everywhere; the notification says where it landed in the plan.
chrome.omnibox.setDefaultSuggestion({
  description: "Add to IntelliPlan — e.g. bio lab due fri 2h"
});

chrome.omnibox.onInputChanged.addListener((text, suggest) => {
  const clean = (text || "").trim();
  chrome.omnibox.setDefaultSuggestion({
    description: clean
      ? "Add to IntelliPlan: " + clean.replace(/[<>&]/g, " ")
      : "Add to IntelliPlan — e.g. bio lab due fri 2h"
  });
  suggest([]);
});

chrome.omnibox.onInputEntered.addListener(async (text) => {
  const clean = (text || "").trim();
  if (!clean) return;
  const stored = await chrome.storage.local.get(["authToken"]);
  const token = stored.authToken;
  const notify = (title, message) => chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon-128.png",
    title,
    message: String(message || "").slice(0, 240)
  });
  if (!token) {
    notify("Sign in to IntelliPlan", "Open the IntelliPlan extension and sign in, then try again.");
    return;
  }
  try {
    const res = await fetch(BASE_URL + "/extension/task/add", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Extension-Token": token },
      body: JSON.stringify({
        text: clean.slice(0, 500),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || ""
      })
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.status === "ok") {
      notify("Added to IntelliPlan", data.message || clean);
    } else {
      notify("Could not add that", data.message || "Please try again.");
    }
  } catch (_e) {
    notify("Could not add that", "No connection to IntelliPlan.");
  }
});
