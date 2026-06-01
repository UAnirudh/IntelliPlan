const BASE_URL = "https://intelliplan.tech";

let currentTab   = "tasks";
let authToken    = null;
let userEmail    = null;
let focusTimer   = null;   // interval id
let focusSeconds = 0;
let focusTotal   = 25 * 60;
let addTaskOpen  = false;

// ── INIT ──────────────────────────────────────────────────────
async function init() {
  const stored = await chrome.storage.local.get(["authToken", "userEmail"]);
  authToken = stored.authToken || null;
  userEmail = stored.userEmail || null;

  if (authToken) {
    showApp();
    loadTab("tasks");
  } else {
    // Try auto-login from browser session cookie before showing auth
    tryAutoLogin();
  }

  // Wire up buttons
  document.getElementById("openAppBtn")?.addEventListener("click", () =>
    chrome.tabs.create({ url: BASE_URL + "/dashboard" })
  );
  document.getElementById("logoutBtn")?.addEventListener("click", logout);
  document.getElementById("qlScheduler")?.addEventListener("click", () =>
    chrome.tabs.create({ url: BASE_URL + "/scheduler" })
  );
  document.getElementById("qlTutor")?.addEventListener("click", () =>
    chrome.tabs.create({ url: BASE_URL + "/tutor" })
  );
  document.getElementById("qlFocus")?.addEventListener("click", toggleFocusMode);

  // Tab bar
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      if (focusTimer !== null) stopFocus();  // exit focus when switching tabs
      currentTab = btn.dataset.tab;
      document.querySelectorAll(".tab-btn").forEach(t => t.classList.remove("active"));
      btn.classList.add("active");
      loadTab(currentTab);
    });
  });

  // Auth form
  document.getElementById("loginModeBtn")?.addEventListener("click",  () => setAuthMode("login"));
  document.getElementById("signupModeBtn")?.addEventListener("click", () => setAuthMode("signup"));
  document.getElementById("authForm")?.addEventListener("submit", handleAuthSubmit);
  document.getElementById("btnGoogle")?.addEventListener("click", handleGoogleLogin);
  document.getElementById("btnSignedIn")?.addEventListener("click", handleGoogleLoginComplete);
  document.getElementById("btnCancelGoogle")?.addEventListener("click", cancelGoogleLogin);
}

// ── AUTO-LOGIN FROM BROWSER SESSION ──────────────────────────
async function tryAutoLogin() {
  const banner = document.getElementById("autoBanner");
  if (banner) banner.classList.remove("hidden");

  try {
    const res = await fetch(BASE_URL + "/extension/session-token", {
      credentials: "include"
    });
    if (res.ok) {
      const data = await res.json();
      if (data.status === "ok" && data.token) {
        authToken = data.token;
        userEmail = data.email || "";
        await chrome.storage.local.set({ authToken, userEmail });
        showApp();
        loadTab("tasks");
        return;
      }
    }
  } catch (_) {}

  // No active session — show auth normally
  if (banner) banner.classList.add("hidden");
}

// ── GOOGLE LOGIN ──────────────────────────────────────────────
function handleGoogleLogin() {
  chrome.tabs.create({ url: BASE_URL + "/login/google" });
  // Switch UI to "waiting" state
  document.getElementById("btnGoogle").classList.add("hidden");
  document.getElementById("emailDivider").classList.add("hidden");
  document.getElementById("emailSection").classList.add("hidden");
  document.getElementById("googleWaiting").classList.remove("hidden");
}

async function handleGoogleLoginComplete() {
  const btn    = document.getElementById("btnSignedIn");
  const status = document.getElementById("authStatus");
  btn.textContent = "Checking…";
  btn.disabled    = true;

  try {
    const res = await fetch(BASE_URL + "/extension/session-token", {
      credentials: "include"
    });
    if (res.ok) {
      const data = await res.json();
      if (data.status === "ok" && data.token) {
        authToken = data.token;
        userEmail = data.email || "";
        await chrome.storage.local.set({ authToken, userEmail });
        showApp();
        loadTab("tasks");
        return;
      }
    }
    if (status) {
      status.textContent = "Login not detected yet — complete Google sign-in in the tab, then try again.";
      status.className = "status err";
    }
  } catch (_) {
    if (status) {
      status.textContent = "Connection error. Make sure you're online.";
      status.className = "status err";
    }
  }

  btn.textContent = "I've signed in ✓";
  btn.disabled    = false;
}

function cancelGoogleLogin() {
  document.getElementById("btnGoogle").classList.remove("hidden");
  document.getElementById("emailDivider").classList.remove("hidden");
  document.getElementById("emailSection").classList.remove("hidden");
  document.getElementById("googleWaiting").classList.add("hidden");
  const status = document.getElementById("authStatus");
  if (status) { status.textContent = ""; status.className = "status"; }
}

// ── AUTH FORM ────────────────────────────────────────────────
function setAuthMode(mode) {
  const isLogin = mode === "login";
  document.getElementById("loginModeBtn")?.classList.toggle("active", isLogin);
  document.getElementById("signupModeBtn")?.classList.toggle("active", !isLogin);
  document.getElementById("nameField")?.classList.toggle("hidden", isLogin);
  const btn = document.getElementById("authSubmitBtn");
  if (btn) btn.textContent = isLogin ? "Login" : "Create Account";
  const s = document.getElementById("authStatus");
  if (s) { s.textContent = ""; s.className = "status"; }
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  const btn      = document.getElementById("authSubmitBtn");
  const status   = document.getElementById("authStatus");
  const email    = document.getElementById("emailInput")?.value.trim() || "";
  const password = document.getElementById("passwordInput")?.value.trim() || "";
  const isLogin  = document.getElementById("loginModeBtn")?.classList.contains("active");
  const endpoint = isLogin ? "/extension/login" : "/extension/register";

  if (btn) { btn.textContent = "…"; btn.disabled = true; }
  if (status) { status.textContent = ""; status.className = "status"; }

  try {
    const body = isLogin ? { email, password } : {
      email, password,
      name: document.getElementById("nameInput")?.value.trim() || ""
    };
    const res  = await fetch(BASE_URL + endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (data.status === "ok") {
      authToken = data.token;
      userEmail = data.email;
      await chrome.storage.local.set({ authToken, userEmail });
      showApp();
      loadTab("tasks");
    } else {
      if (status) { status.textContent = data.message || "Authentication failed"; status.className = "status err"; }
    }
  } catch (_) {
    if (status) { status.textContent = "Connection error — is IntelliPlan reachable?"; status.className = "status err"; }
  }

  if (btn) {
    btn.textContent = isLogin ? "Login" : "Create Account";
    btn.disabled = false;
  }
}

// ── LOGOUT ───────────────────────────────────────────────────
async function logout() {
  try {
    await fetch(BASE_URL + "/extension/logout", {
      method: "POST",
      headers: { "X-Extension-Token": authToken }
    });
  } catch (_) {}
  authToken = null;
  userEmail = null;
  await chrome.storage.local.remove(["authToken", "userEmail"]);
  if (focusTimer !== null) stopFocus();
  showAuth();
}

// ── SHOW / HIDE VIEWS ────────────────────────────────────────
function showApp() {
  document.getElementById("authView").classList.add("hidden");
  document.getElementById("appView").classList.remove("hidden");
  // Set avatar initial
  const av = document.getElementById("userAvatar");
  if (av && userEmail) av.textContent = userEmail[0].toUpperCase();
}

function showAuth() {
  document.getElementById("authView").classList.remove("hidden");
  document.getElementById("appView").classList.add("hidden");
  cancelGoogleLogin();
}

// ── API HELPER ───────────────────────────────────────────────
async function apiGet(endpoint) {
  try {
    const res = await fetch(BASE_URL + endpoint, {
      headers: { "X-Extension-Token": authToken }
    });
    if (res.status === 401) {
      authToken = null;
      await chrome.storage.local.remove(["authToken", "userEmail"]);
      showAuth();
      return null;
    }
    return await res.json();
  } catch (_) { return null; }
}

// ── TAB LOADING ───────────────────────────────────────────────
async function loadTab(tab) {
  const content = document.getElementById("content");
  if (!content) return;
  if (focusTimer !== null && tab !== "focus") {
    renderFocusTimer(content);
    return;
  }
  content.innerHTML = `<div class="loading"><div class="spinner"></div><div class="loading-text">Loading…</div></div>`;
  if (tab === "tasks")    await loadTasks(content);
  else if (tab === "schedule") await loadSchedule(content);
  else if (tab === "grades")   await loadGrades(content);
}

// ── TASKS ────────────────────────────────────────────────────
async function loadTasks(content) {
  const raw = await apiGet("/extension/tasks");
  if (!raw) {
    content.innerHTML = `<div class="empty"><div class="empty-icon">⚠️</div><div class="empty-text">Could not load tasks. Check your connection.</div></div>`;
    return;
  }

  const data     = dedupeTaskBuckets(raw);
  const overdue  = data.overdue?.length  || 0;
  const today    = data.today?.length    || 0;
  const upcoming = data.upcoming?.length || 0;

  // Update tab count badge
  const tc = document.getElementById("tcTasks");
  if (tc) tc.textContent = (overdue + today) || "–";

  if (!overdue && !today && !upcoming) {
    content.innerHTML = `<div class="empty"><div class="empty-icon">✅</div><div class="empty-text">All clear! No tasks right now.</div></div>`;
    return;
  }

  let html = `
    <div class="stats-row fade-in">
      <div class="stat-box"><div class="stat-num" style="color:var(--high)">${overdue}</div><div class="stat-lbl">Overdue</div></div>
      <div class="stat-box"><div class="stat-num" style="color:var(--accent)">${today}</div><div class="stat-lbl">Today</div></div>
      <div class="stat-box"><div class="stat-num" style="color:var(--low)">${upcoming}</div><div class="stat-lbl">Upcoming</div></div>
    </div>

    <button class="add-task-toggle" id="addTaskToggle" type="button">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      Quick add task
    </button>
    <div id="addTaskWrap" class="add-task-wrap hidden">
      <div class="add-task-row">
        <input type="text" class="add-task-input" id="addTaskInput" placeholder="Task title…" />
        <button class="add-task-submit" id="addTaskSubmit" type="button">Add</button>
      </div>
      <div id="addTaskStatus" class="status" style="margin-top:6px;text-align:left;"></div>
    </div>`;

  if (data.overdue?.length) {
    html += `<div class="sec-label">⚠ Overdue</div>`;
    html += data.overdue.map(t => taskCard(t, "high")).join("");
  }
  if (data.today?.length) {
    html += `<div class="sec-label">📅 Due Today</div>`;
    html += data.today.map(t => taskCard(t, t.priority?.toLowerCase() || "medium")).join("");
  }
  if (data.upcoming?.length) {
    html += `<div class="sec-label">🗓 Upcoming</div>`;
    html += data.upcoming.slice(0, 5).map(t => taskCard(t, t.priority?.toLowerCase() || "low")).join("");
    if (data.upcoming.length > 5) {
      html += `<div style="text-align:center;font-size:0.72rem;color:var(--muted);padding:6px 0 4px;">+${data.upcoming.length - 5} more in app</div>`;
    }
  }

  html += `
    <div style="text-align:center;margin-top:10px;">
      <button class="link-btn" onclick="startFocusMode()" type="button">⏱ Start 25-min focus session</button>
    </div>`;

  content.innerHTML = html;

  // Wire quick-add
  document.getElementById("addTaskToggle")?.addEventListener("click", () => {
    addTaskOpen = !addTaskOpen;
    const wrap = document.getElementById("addTaskWrap");
    if (wrap) wrap.classList.toggle("hidden", !addTaskOpen);
    if (addTaskOpen) document.getElementById("addTaskInput")?.focus();
  });
  document.getElementById("addTaskSubmit")?.addEventListener("click", submitQuickTask);
  document.getElementById("addTaskInput")?.addEventListener("keydown", e => {
    if (e.key === "Enter") submitQuickTask();
  });

  // Dismiss handlers
  content.querySelectorAll(".dismiss-btn").forEach(btn => {
    btn.addEventListener("click", async e => {
      e.stopPropagation();
      const title = btn.dataset.title || "";
      await fetch(BASE_URL + "/extension/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Extension-Token": authToken },
        body: JSON.stringify({ title })
      });
      const card = btn.closest(".task-card");
      if (card) { card.style.opacity = "0"; card.style.transform = "translateX(8px)"; card.style.transition = "all 0.2s"; setTimeout(() => card.remove(), 200); }
    });
  });
}

async function submitQuickTask() {
  const input  = document.getElementById("addTaskInput");
  const status = document.getElementById("addTaskStatus");
  const btn    = document.getElementById("addTaskSubmit");
  const title  = input?.value.trim();
  if (!title) return;

  if (btn) { btn.textContent = "…"; btn.disabled = true; }
  try {
    const res  = await fetch(BASE_URL + "/api/tasks/quick-add", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Extension-Token": authToken },
      body: JSON.stringify({ title })
    });
    const data = await res.json();
    if (data.status === "ok" || res.ok) {
      if (status) { status.textContent = "Task added!"; status.className = "status ok"; }
      if (input) input.value = "";
    } else {
      if (status) { status.textContent = data.message || "Could not add task."; status.className = "status err"; }
    }
  } catch (_) {
    if (status) { status.textContent = "Connection error."; status.className = "status err"; }
  }
  if (btn) { btn.textContent = "Add"; btn.disabled = false; }
}

// ── SCHEDULE ─────────────────────────────────────────────────
async function loadSchedule(content) {
  const data = await apiGet("/extension/schedule");
  if (!data || data.status !== "ok") {
    content.innerHTML = `
      <div class="empty">
        <div class="empty-icon">📅</div>
        <div class="empty-text">No saved schedule yet.</div>
        <button class="link-btn" type="button" onclick="chrome.tabs.create({url:'${BASE_URL}/scheduler'})">Generate one ↗</button>
      </div>`;
    return;
  }

  const todayStr = new Date().toISOString().split("T")[0];
  const todayDay = data.data?.schedule?.find(d => d.date === todayStr);
  let html = `<div class="sec-label">📅 ${escapeHtml(data.name)}</div>`;

  if (!todayDay?.blocks?.length) {
    html += `<div class="empty"><div class="empty-text">No blocks scheduled for today.</div></div>`;
    content.innerHTML = html;
    return;
  }

  const studyBlocks = todayDay.blocks.filter(b => !b.is_break);
  html += `
    <div class="stats-row fade-in" style="margin-bottom:10px;">
      <div class="stat-box"><div class="stat-num">${studyBlocks.length}</div><div class="stat-lbl">Blocks</div></div>
      <div class="stat-box"><div class="stat-num">${todayDay.total_hours || "—"}h</div><div class="stat-lbl">Study</div></div>
      <div class="stat-box"><div class="stat-num" style="font-size:0.72rem;padding-top:8px;color:var(--muted);text-transform:capitalize;">${escapeHtml(todayDay.workload_level || "—")}</div><div class="stat-lbl">Load</div></div>
    </div>`;

  todayDay.blocks.forEach(block => {
    if (block.is_break) {
      html += `
        <div class="block-card is-break">
          <div class="block-time">${escapeHtml(block.time_slot?.split(" - ")[0] || "")}</div>
          <div class="block-info"><div class="block-title">☕ Break</div></div>
          <div class="block-dur">${escapeHtml(String(block.duration_minutes))}m</div>
        </div>`;
    } else {
      html += `
        <div class="block-card fade-in">
          <div class="block-time">${escapeHtml(block.time_slot?.split(" - ")[0] || "")}</div>
          <div class="block-info">
            <div class="block-title">${escapeHtml(block.assignment)}</div>
            <div class="block-course">📚 ${escapeHtml(block.course)}</div>
          </div>
          <div class="block-dur">${escapeHtml(String(block.duration_minutes))}m</div>
        </div>`;
    }
  });

  if (todayDay.daily_tip) {
    html += `<div class="daily-tip">💡 ${escapeHtml(todayDay.daily_tip)}</div>`;
  }

  content.innerHTML = html;
}

// ── GRADES ───────────────────────────────────────────────────
async function loadGrades(content) {
  const raw = await apiGet("/extension/grades");
  if (!raw) {
    content.innerHTML = `<div class="empty"><div class="empty-icon">📊</div><div class="empty-text">Could not load grades.</div></div>`;
    return;
  }

  const data = dedupeTasks(raw);
  if (!data.length) {
    content.innerHTML = `<div class="empty"><div class="empty-icon">📊</div><div class="empty-text">No grades available yet.<br><small style="font-size:0.72rem">Connect Canvas or StudentVue in Settings.</small></div></div>`;
    return;
  }

  // Compute average GPA
  const withPct = data.filter(g => g.percentage != null && g.percentage !== undefined);
  const avg     = withPct.length ? Math.round(withPct.reduce((s, g) => s + g.percentage, 0) / withPct.length) : null;

  let html = `
    <div class="gpa-banner fade-in">
      <div>
        <div class="gpa-label">GPA Estimate</div>
        <div class="gpa-value">${avg !== null ? avg + "%" : "N/A"}</div>
      </div>
      <div class="gpa-right">
        <div class="gpa-courses">${data.length} course${data.length !== 1 ? "s" : ""}</div>
      </div>
    </div>
    <div class="sec-label">📚 Course Grades</div>`;

  data.forEach(g => {
    const colors = gradeColor(g.letter);
    const bar    = Math.min(g.percentage || 0, 100);
    html += `
      <div class="grade-row fade-in">
        <div class="grade-info">
          <div class="grade-name">${escapeHtml(g.course)}</div>
          <div class="grade-bar-wrap"><div class="grade-bar" style="width:${bar}%;background:${colors.color};"></div></div>
          <div class="grade-pct">${g.percentage != null ? escapeHtml(String(g.percentage)) + "%" : "N/A"}</div>
        </div>
        <div class="grade-badge" style="background:${colors.bg};color:${colors.color};">${escapeHtml(g.letter || "—")}</div>
      </div>`;
  });

  content.innerHTML = html;
}

// ── FOCUS TIMER ───────────────────────────────────────────────
function toggleFocusMode() {
  if (focusTimer !== null) {
    stopFocus();
  } else {
    startFocusMode();
  }
}

function startFocusMode() {
  focusTotal   = 25 * 60;
  focusSeconds = 0;
  focusTimer   = setInterval(tickFocus, 1000);

  // Switch ql-focus button style
  document.getElementById("qlFocus")?.classList.add("focus-active");

  const content = document.getElementById("content");
  if (content) renderFocusTimer(content);
}

function stopFocus() {
  clearInterval(focusTimer);
  focusTimer   = null;
  focusSeconds = 0;
  document.getElementById("qlFocus")?.classList.remove("focus-active");
  loadTab(currentTab);
}

function tickFocus() {
  focusSeconds++;
  if (focusSeconds >= focusTotal) {
    clearInterval(focusTimer);
    focusTimer = null;
    document.getElementById("qlFocus")?.classList.remove("focus-active");
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icons/icon-128.png",
      title: "IntelliPlan — Focus Session Complete!",
      message: "Great work! Take a 5-minute break.",
      priority: 2
    });
    loadTab(currentTab);
    return;
  }
  updateFocusDisplay();
}

function renderFocusTimer(content) {
  const remaining = focusTotal - focusSeconds;
  const mins      = Math.floor(remaining / 60);
  const secs      = remaining % 60;
  const fmtTime   = `${String(mins).padStart(2,"0")}:${String(secs).padStart(2,"0")}`;
  const circumference = 2 * Math.PI * 54;
  const progress  = focusSeconds / focusTotal;
  const dashOffset = circumference * (1 - progress);

  content.innerHTML = `
    <div class="focus-wrap fade-in">
      <div class="focus-label">Focus Session</div>
      <div class="focus-ring">
        <svg width="130" height="130" viewBox="0 0 130 130">
          <circle class="track" cx="65" cy="65" r="54" stroke="var(--border)" fill="none" stroke-width="6"/>
          <circle class="prog"  cx="65" cy="65" r="54" stroke="var(--accent)" fill="none" stroke-width="6"
            stroke-dasharray="${circumference.toFixed(2)}"
            stroke-dashoffset="${dashOffset.toFixed(2)}"
            id="focusProgCircle"
          />
        </svg>
        <div class="focus-time" id="focusTimeDisplay">${fmtTime}</div>
      </div>
      <div class="focus-sub">Stay on task — you've got this! 💪<br>Notifications suppressed until the session ends.</div>
      <div class="focus-actions">
        <button class="focus-stop" onclick="stopFocus()" type="button">✕ Stop</button>
        <button class="focus-open" onclick="chrome.tabs.create({url:'${BASE_URL}/focus'})" type="button">Open Full Timer ↗</button>
      </div>
    </div>`;
}

function updateFocusDisplay() {
  const remaining = focusTotal - focusSeconds;
  const mins      = Math.floor(remaining / 60);
  const secs      = remaining % 60;
  const fmtTime   = `${String(mins).padStart(2,"0")}:${String(secs).padStart(2,"0")}`;

  const el = document.getElementById("focusTimeDisplay");
  if (el) el.textContent = fmtTime;

  const circumference = 2 * Math.PI * 54;
  const dashOffset    = circumference * (1 - focusSeconds / focusTotal);
  const circle = document.getElementById("focusProgCircle");
  if (circle) circle.setAttribute("stroke-dashoffset", dashOffset.toFixed(2));
}

// ── HELPERS ───────────────────────────────────────────────────
function escapeHtml(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function normalizeText(v) { return String(v ?? "").trim().toLowerCase(); }

function taskKey(t) {
  return [normalizeText(t?.title), normalizeText(t?.course),
          normalizeText(t?.due_date), normalizeText(t?.priority)].join("|");
}

function getSourceScore(src) {
  return ({ manual:5, notion:4, canvas:3, studentvue:2, studentvue_missing:1 })[normalizeText(src)] || 0;
}

function getQualityScore(t) {
  let s = 0;
  if (String(t?.id ?? "").trim())              s += 2;
  if (String(t?.notes ?? "").trim())           s += 1;
  if (String(t?.course ?? "").trim())          s += 1;
  if (String(t?.due_date ?? "").trim())        s += 1;
  if (String(t?.estimated_time ?? "").trim())  s += 1;
  return s;
}

function dedupeTasks(tasks) {
  const map = new Map();
  (tasks || []).forEach(t => {
    if (!t) return;
    const k = taskKey(t);
    if (!map.has(k)) { map.set(k, t); return; }
    const ex = map.get(k);
    map.set(k, getSourceScore(t.source) > getSourceScore(ex.source) ||
      (getSourceScore(t.source) === getSourceScore(ex.source) && getQualityScore(t) > getQualityScore(ex))
        ? t : ex);
  });
  return Array.from(map.values());
}

function dedupeTaskBuckets(data) {
  const buckets = { overdue: [], today: [], upcoming: [] };
  const seen    = new Map();
  ["overdue","today","upcoming"].forEach((bName, bIdx) => {
    (data?.[bName] || []).forEach((task, iIdx) => {
      if (!task) return;
      const k = taskKey(task);
      const c = { task, bName, bIdx, iIdx, src: getSourceScore(task.source), q: getQualityScore(task) };
      if (!seen.has(k)) { seen.set(k, c); return; }
      const ex = seen.get(k);
      if (c.bIdx < ex.bIdx || (c.bIdx === ex.bIdx && (c.src > ex.src || (c.src === ex.src && c.q > ex.q)))) seen.set(k, c);
    });
  });
  for (const e of seen.values()) buckets[e.bName].push(e.task);
  return buckets;
}

function taskCard(t, priority) {
  const isMissing  = t.is_missing || t.source === "studentvue_missing";
  const safeTitle  = escapeHtml(t.title);
  const safeCourse = escapeHtml(t.course);
  const safeTitleA = escapeHtml(t.title).replace(/"/g, "&quot;");
  const missing    = isMissing ? ` <span style="color:var(--high);font-size:0.63rem;font-weight:600;">(${escapeHtml(t.score_label || "Missing")})</span>` : "";
  return `
    <div class="task-card ${isMissing ? "missing" : priority}">
      <div class="task-card-head">
        <div class="task-title">${safeTitle}${missing}</div>
        <button class="dismiss-btn" data-title="${safeTitleA}" title="Dismiss" type="button">✓</button>
      </div>
      <div class="task-meta">
        ${t.due_date ? `<span class="pill due">📅 ${escapeHtml(t.due_date)}</span>` : ""}
        <span class="pill ${escapeHtml(priority)}">${escapeHtml(t.priority || "Medium")}</span>
        ${t.course && t.course !== "Unknown" ? `<span class="pill course">${safeCourse}</span>` : ""}
      </div>
    </div>`;
}

function gradeColor(letter) {
  if (!letter) return { bg:"#f1f5f9", color:"#64748b" };
  if (letter.startsWith("A")) return { bg:"#dcfce7", color:"#16a34a" };
  if (letter.startsWith("B")) return { bg:"#dbeafe", color:"#2563eb" };
  if (letter.startsWith("C")) return { bg:"#fef3c7", color:"#b45309" };
  return { bg:"#fee2e2", color:"#dc2626" };
}

// ── START ─────────────────────────────────────────────────────
init();
