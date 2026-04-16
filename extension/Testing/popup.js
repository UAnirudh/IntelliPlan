const BASE_URL = "https://intelliplan.up.railway.app";
let currentTab = "tasks";
let authToken = null;
let userEmail = null;

// ── INIT ─────────────────────────────────────────────────────
async function init() {
  const stored = await chrome.storage.local.get(["authToken", "userEmail"]);
  authToken = stored.authToken || null;
  userEmail = stored.userEmail || null;

  if (authToken) {
    showApp();
    loadTab("tasks");
  } else {
    showAuth();
  }

  document.getElementById("openAppBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: BASE_URL + "/dashboard" });
  });

  document.getElementById("logoutBtn").addEventListener("click", logout);

  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
      currentTab = tab.dataset.tab;
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      loadTab(currentTab);
    });
  });

  // Auth form mode toggle
  document.getElementById("loginModeBtn").addEventListener("click", () => setAuthMode("login"));
  document.getElementById("signupModeBtn").addEventListener("click", () => setAuthMode("signup"));
  document.getElementById("authForm").addEventListener("submit", handleAuthSubmit);
}

function setAuthMode(mode) {
  const isLogin = mode === "login";
  document.getElementById("loginModeBtn").classList.toggle("active", isLogin);
  document.getElementById("signupModeBtn").classList.toggle("active", !isLogin);
  document.getElementById("nameField").classList.toggle("hidden", isLogin);
  document.getElementById("authSubmitBtn").textContent = isLogin ? "Login" : "Create Account";
  document.getElementById("authStatus").textContent = "";
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  const btn = document.getElementById("authSubmitBtn");
  const status = document.getElementById("authStatus");
  const email = document.getElementById("emailInput").value.trim();
  const password = document.getElementById("passwordInput").value.trim();
  const isLogin = document.getElementById("loginModeBtn").classList.contains("active");
  const endpoint = isLogin ? "/extension/login" : "/extension/register";

  btn.textContent = "...";
  btn.disabled = true;
  status.textContent = "";
  status.className = "status";

  try {
    const res = await fetch(BASE_URL + endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password })
    });
    const data = await res.json();

    if (data.status === "ok") {
      authToken = data.token;
      userEmail = data.email;
      await chrome.storage.local.set({ authToken, userEmail });
      showApp();
      loadTab("tasks");
    } else {
      status.textContent = data.message || "Authentication failed";
      status.className = "status error";
    }
  } catch (err) {
    status.textContent = "Connection error — is IntelliPlan running?";
    status.className = "status error";
  }

  btn.textContent = isLogin ? "Login" : "Create Account";
  btn.disabled = false;
}

async function logout() {
  try {
    await fetch(BASE_URL + "/extension/logout", {
      method: "POST",
      headers: { "X-Extension-Token": authToken }
    });
  } catch (e) {}
  authToken = null;
  userEmail = null;
  await chrome.storage.local.remove(["authToken", "userEmail"]);
  showAuth();
}

function showApp() {
  document.getElementById("authView").classList.add("hidden");
  document.getElementById("appView").classList.remove("hidden");
  document.getElementById("logoutBtn").classList.remove("hidden");
  if (userEmail) {
    document.getElementById("logoutBtn").title = userEmail;
  }
}

function showAuth() {
  document.getElementById("authView").classList.remove("hidden");
  document.getElementById("appView").classList.add("hidden");
  document.getElementById("logoutBtn").classList.add("hidden");
}

// ── API CALLS ─────────────────────────────────────────────────
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
  } catch (e) {
    return null;
  }
}

// ── TABS ──────────────────────────────────────────────────────
async function loadTab(tab) {
  const content = document.getElementById("content");
  content.innerHTML = `<div class="loading"><div class="spinner"></div>Loading...</div>`;
  if (tab === "tasks") await loadTasks(content);
  else if (tab === "schedule") await loadSchedule(content);
  else if (tab === "grades") await loadGrades(content);
}

async function loadTasks(content) {
  const data = await apiGet("/extension/tasks");
  if (!data) {
    content.innerHTML = `<div class="empty">Could not load tasks. Check your connection.</div>`;
    return;
  }

  const overdue = data.overdue?.length || 0;
  const today = data.today?.length || 0;
  const upcoming = data.upcoming?.length || 0;

  if (!overdue && !today && !upcoming) {
    content.innerHTML = `<div class="empty">✅ All clear — no tasks!</div>`;
    return;
  }

  let html = `
    <div class="stats-row">
      <div class="stat-box">
        <div class="stat-num" style="color:#ef4444;">${overdue}</div>
        <div class="stat-lbl">Overdue</div>
      </div>
      <div class="stat-box">
        <div class="stat-num" style="color:#3b82f6;">${today}</div>
        <div class="stat-lbl">Today</div>
      </div>
      <div class="stat-box">
        <div class="stat-num" style="color:#22c55e;">${upcoming}</div>
        <div class="stat-lbl">Upcoming</div>
      </div>
    </div>`;

  if (data.overdue?.length) {
    html += `<div class="section-label">⚠ Overdue</div>`;
    html += data.overdue.map(t => taskCard(t, "high")).join("");
  }
  if (data.today?.length) {
    html += `<div class="section-label">📅 Due Today</div>`;
    html += data.today.map(t => taskCard(t, t.priority?.toLowerCase() || "medium")).join("");
  }
  if (data.upcoming?.length) {
    html += `<div class="section-label">🗓 Upcoming</div>`;
    html += data.upcoming.slice(0, 6).map(t => taskCard(t, t.priority?.toLowerCase() || "low")).join("");
    if (data.upcoming.length > 6) {
      html += `<div style="text-align:center;font-size:0.75rem;color:#94a3b8;padding:8px 0;">+${data.upcoming.length - 6} more in app</div>`;
    }
  }

  content.innerHTML = html;

  // Attach dismiss handlers
  content.querySelectorAll(".dismiss-task").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const title = btn.dataset.title;
      await fetch(BASE_URL + "/extension/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Extension-Token": authToken },
        body: JSON.stringify({ title })
      });
      btn.closest(".task-card").style.opacity = "0";
      setTimeout(() => { btn.closest(".task-card").remove(); }, 200);
    });
  });
}

async function loadSchedule(content) {
  const data = await apiGet("/extension/schedule");
  if (!data || data.status !== "ok") {
    content.innerHTML = `
      <div class="empty">
        <div style="font-size:1.5rem;margin-bottom:8px;">📅</div>
        No saved schedule.<br>
        <button class="link-btn" style="margin-top:8px;" onclick="chrome.tabs.create({url:'${BASE_URL}/scheduler'})">Generate one ↗</button>
      </div>`;
    return;
  }

  const todayStr = new Date().toISOString().split("T")[0];
  const todayDay = data.data?.schedule?.find(d => d.date === todayStr);
  let html = `<div class="section-label">📅 ${data.name}</div>`;

  if (!todayDay?.blocks?.length) {
    html += `<div class="empty">No blocks for today.</div>`;
    content.innerHTML = html;
    return;
  }

  const studyBlocks = todayDay.blocks.filter(b => !b.is_break);
  html += `
    <div class="stats-row">
      <div class="stat-box">
        <div class="stat-num">${studyBlocks.length}</div>
        <div class="stat-lbl">Blocks</div>
      </div>
      <div class="stat-box">
        <div class="stat-num">${todayDay.total_hours || "—"}h</div>
        <div class="stat-lbl">Total</div>
      </div>
      <div class="stat-box">
        <div class="stat-num" style="font-size:0.7rem;padding-top:6px;color:#64748b;text-transform:capitalize;">${todayDay.workload_level || "—"}</div>
        <div class="stat-lbl">Load</div>
      </div>
    </div>`;

  todayDay.blocks.forEach(block => {
    if (block.is_break) {
      html += `
        <div class="block-card" style="opacity:0.5;border-left-color:#cbd5e1;">
          <div class="block-time">${block.time_slot?.split(" - ")[0] || ""}</div>
          <div class="block-info"><div class="block-title">☕ Break</div></div>
          <div class="block-dur">${block.duration_minutes}m</div>
        </div>`;
    } else {
      html += `
        <div class="block-card">
          <div class="block-time">${block.time_slot?.split(" - ")[0] || ""}</div>
          <div class="block-info">
            <div class="block-title">${block.assignment}</div>
            <div class="block-course">📚 ${block.course}</div>
          </div>
          <div class="block-dur">${block.duration_minutes}m</div>
        </div>`;
    }
  });

  if (todayDay.daily_tip) {
    html += `<div style="background:#fffbeb;border:1px solid #fef3c7;border-radius:10px;padding:8px 10px;font-size:0.75rem;color:#92400e;margin-top:8px;">✨ ${todayDay.daily_tip}</div>`;
  }

  content.innerHTML = html;
}

async function loadGrades(content) {
  const data = await apiGet("/extension/grades");
  if (!data) {
    content.innerHTML = `<div class="empty">Could not load grades.</div>`;
    return;
  }
  if (!data.length) {
    content.innerHTML = `<div class="empty">No grades available yet.</div>`;
    return;
  }

  let html = `<div class="section-label">📊 Current Grades</div>`;
  data.forEach(g => {
    const colors = gradeColor(g.letter);
    const bar = Math.min(g.percentage || 0, 100);
    html += `
      <div class="grade-row">
        <div style="flex:1;min-width:0;">
          <div class="grade-name" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:0.8rem;font-weight:600;color:#0f172a;">${g.course}</div>
          <div style="height:4px;background:#e2eaf5;border-radius:2px;margin-top:4px;overflow:hidden;">
            <div style="height:100%;width:${bar}%;background:${colors.color};border-radius:2px;"></div>
          </div>
          <div style="font-size:0.65rem;color:#94a3b8;margin-top:2px;">${g.percentage !== null ? g.percentage + "%" : "N/A"}</div>
        </div>
        <div style="font-size:0.88rem;font-weight:700;padding:3px 10px;border-radius:8px;background:${colors.bg};color:${colors.color};margin-left:10px;">${g.letter}</div>
      </div>`;
  });

  content.innerHTML = html;
}

// ── HELPERS ───────────────────────────────────────────────────
function taskCard(t, priority) {
  const isMissing = t.is_missing || t.source === "studentvue_missing";
  const safeTitle = t.title.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  return `
    <div class="task-card ${isMissing ? "missing" : priority}">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:6px;">
        <div class="task-title">${t.title}${isMissing ? ` <span style="color:#ef4444;font-size:0.68rem;">(${t.score_label || "Missing"})</span>` : ""}</div>
        <button class="dismiss-task" data-title="${safeTitle}" style="background:none;border:none;cursor:pointer;color:#94a3b8;font-size:0.7rem;padding:0;flex-shrink:0;line-height:1;">✓</button>
      </div>
      <div class="task-meta">
        ${t.due_date ? `<span class="pill due">📅 ${t.due_date}</span>` : ""}
        <span class="pill ${priority}">${t.priority || "Medium"}</span>
        ${t.course && t.course !== "Unknown" ? `<span class="pill course">${t.course}</span>` : ""}
      </div>
    </div>`;
}

function gradeColor(letter) {
  if (!letter) return { bg: "#f1f5f9", color: "#64748b" };
  if (letter.startsWith("A")) return { bg: "#f0fdf4", color: "#22c55e" };
  if (letter.startsWith("B")) return { bg: "#dbeafe", color: "#3b82f6" };
  if (letter.startsWith("C")) return { bg: "#fffbeb", color: "#f59e0b" };
  return { bg: "#fef2f2", color: "#ef4444" };
}

// ── MISSING CSS for stats/grade rows ─────────────────────────
const style = document.createElement("style");
style.textContent = `
  .stats-row { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin-bottom:12px; }
  .stat-box { background:white; border:1px solid #e2eaf5; border-radius:10px; padding:8px; text-align:center; }
  .stat-num { font-size:1.2rem; font-weight:700; color:#0f172a; }
  .stat-lbl { font-size:0.6rem; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; color:#94a3b8; }
  .grade-row { background:white; border:1px solid #e2eaf5; border-radius:10px; padding:10px 12px; margin-bottom:6px; display:flex; align-items:center; gap:10px; }
  .link-btn { border:none; background:transparent; color:#2563eb; cursor:pointer; font-weight:700; font-size:0.82rem; }
`;
document.head.appendChild(style);

// ── START ─────────────────────────────────────────────────────
init();