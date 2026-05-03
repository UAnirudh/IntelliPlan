const BASE_URL = "https://intelli-plan.up.railway.app";

const AUTH_ENDPOINTS = {
  login: "/api/auth/login",
  register: "/api/auth/register",
  me: "/api/auth/me",
  logout: "/api/auth/logout"
};

const STORAGE_KEYS = {
  token: "intelliplan_token",
  user: "intelliplan_user"
};

let currentTab = "tasks";
let authMode = "login";

function openApp() {
  chrome.tabs.create({ url: BASE_URL + "/dashboard" });
}

function setStatus(text, cls = "") {
  const el = document.getElementById("authStatus");
  if (!el) return;
  el.className = `status ${cls}`.trim();
  el.textContent = text || "";
}

function showAuthMode(mode) {
  authMode = mode;

  const loginBtn = document.getElementById("loginModeBtn");
  const signupBtn = document.getElementById("signupModeBtn");
  const nameField = document.getElementById("nameField");
  const submitBtn = document.getElementById("authSubmitBtn");
  const passwordInput = document.getElementById("passwordInput");

  if (loginBtn) loginBtn.classList.toggle("active", mode === "login");
  if (signupBtn) signupBtn.classList.toggle("active", mode === "signup");

  if (nameField) {
    if (mode === "signup") nameField.classList.remove("hidden");
    else nameField.classList.add("hidden");
  }

  if (submitBtn) submitBtn.textContent = mode === "login" ? "Login" : "Create account";
  if (passwordInput) passwordInput.autocomplete = mode === "login" ? "current-password" : "new-password";

  setStatus("");
}

function getStorage(keys) {
  return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
}

function setStorage(items) {
  return new Promise((resolve) => chrome.storage.local.set(items, resolve));
}

function removeStorage(keys) {
  return new Promise((resolve) => chrome.storage.local.remove(keys, resolve));
}

async function getSession() {
  const result = await getStorage([STORAGE_KEYS.token, STORAGE_KEYS.user]);
  return {
    token: result[STORAGE_KEYS.token] || null,
    user: result[STORAGE_KEYS.user] || null
  };
}

async function setSession(token, user) {
  await setStorage({
    [STORAGE_KEYS.token]: token,
    [STORAGE_KEYS.user]: user
  });
}

async function clearSession() {
  await removeStorage([STORAGE_KEYS.token, STORAGE_KEYS.user]);
}

async function apiFetch(path, options = {}) {
  const session = await getSession();
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {})
  };

  if (session.token) {
    headers.Authorization = `Bearer ${session.token}`;
  }

  return fetch(BASE_URL + path, {
    credentials: "omit",
    ...options,
    headers
  });
}

async function authRequest(mode, payload) {
  const path = AUTH_ENDPOINTS[mode];
  const res = await fetch(BASE_URL + path, {
    method: "POST",
    credentials: "omit",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    data = null;
  }

  if (!res.ok) {
    throw new Error(data?.message || data?.error || `HTTP ${res.status}`);
  }

  const token =
    data?.token ||
    data?.access_token ||
    data?.accessToken ||
    data?.jwt ||
    data?.session_token ||
    data?.sessionToken ||
    null;

  const user =
    data?.user ||
    data?.account ||
    data?.profile ||
    {
      name: data?.name || payload.name || "",
      email: data?.email || payload.email || ""
    };

  if (!token) {
    throw new Error("Auth succeeded, but no token was returned.");
  }

  return { token, user, data };
}

function gradeColor(letter) {
  if (!letter) return { bg: "#f1f5f9", color: "#64748b" };
  if (String(letter).startsWith("A")) return { bg: "#f0fdf4", color: "#22c55e" };
  if (String(letter).startsWith("B")) return { bg: "#dbeafe", color: "#3b82f6" };
  if (String(letter).startsWith("C")) return { bg: "#fffbeb", color: "#f59e0b" };
  return { bg: "#fef2f2", color: "#ef4444" };
}

function notConnected() {
  return `
    <div class="empty">
      <div style="font-size:2rem;margin-bottom:12px;">🔗</div>
      You are not signed in.
    </div>`;
}

function taskCard(t, priority) {
  const isMissing = t.is_missing || t.source === "studentvue_missing";
  return `
    <div class="task-card ${isMissing ? "missing" : priority}">
      <div class="task-title">${t.title}${isMissing ? ` <span style="color:#ef4444;font-size:0.68rem;">(${t.score_label || "Missing"})</span>` : ""}</div>
      <div class="task-meta">
        ${t.due_date ? `<span class="pill due">📅 ${t.due_date}</span>` : ""}
        <span class="pill ${priority}">${t.priority || "Medium"}</span>
        ${t.course && t.course !== "Unknown" ? `<span class="pill course">${t.course}</span>` : ""}
        ${isMissing ? `<span class="pill missing">Missing</span>` : ""}
      </div>
    </div>`;
}

async function loadTasks(content) {
  const res = await apiFetch("/tasks/unified", { method: "GET" });
  if (!res.ok) {
    content.innerHTML = notConnected();
    return;
  }

  const data = await res.json();

  const overdue = data.overdue || [];
  const today = data.today || [];
  const upcoming = data.upcoming || [];
  const all = [...overdue, ...today, ...upcoming];

  if (!all.length) {
    content.innerHTML = `<div class="empty">✅ All clear — no tasks!</div>`;
    return;
  }

  let html = `
    <div class="stats-row">
      <div class="stat-box">
        <div class="stat-num" style="color:#ef4444;">${overdue.length}</div>
        <div class="stat-lbl">Overdue</div>
      </div>
      <div class="stat-box">
        <div class="stat-num" style="color:#3b82f6;">${today.length}</div>
        <div class="stat-lbl">Today</div>
      </div>
      <div class="stat-box">
        <div class="stat-num" style="color:#22c55e;">${upcoming.length}</div>
        <div class="stat-lbl">Upcoming</div>
      </div>
    </div>
  `;

  if (overdue.length) {
    html += `<div class="section-label">⚠ Overdue</div>`;
    html += overdue.map(t => taskCard(t, "high")).join("");
  }

  if (today.length) {
    html += `<div class="section-label">📅 Due Today</div>`;
    html += today.map(t => taskCard(t, (t.priority || "medium").toLowerCase())).join("");
  }

  if (upcoming.length) {
    html += `<div class="section-label">🗓 Upcoming</div>`;
    html += upcoming.slice(0, 5).map(t => taskCard(t, (t.priority || "low").toLowerCase())).join("");
    if (upcoming.length > 5) {
      html += `<div style="text-align:center;font-size:0.75rem;color:#94a3b8;padding:8px;">+${upcoming.length - 5} more — <a href="#" id="openAppLink" style="color:#3b82f6;">open app</a></div>`;
    }
  }

  content.innerHTML = html;
  const openAppLink = document.getElementById("openAppLink");
  if (openAppLink) {
    openAppLink.addEventListener("click", (e) => {
      e.preventDefault();
      openApp();
    });
  }
}

async function loadSchedule(content) {
  const res = await apiFetch("/schedule/saved", { method: "GET" });
  if (!res.ok) {
    content.innerHTML = `
      <div class="empty">
        <div style="font-size:1.5rem;margin-bottom:8px;">📅</div>
        No saved schedule.<br>
        <a href="#" id="openSchedulerLink" style="color:#3b82f6;font-size:0.82rem;">Generate one in IntelliPlan ↗</a>
      </div>`;

    const link = document.getElementById("openSchedulerLink");
    if (link) {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        chrome.tabs.create({ url: BASE_URL + "/scheduler" });
      });
    }
    return;
  }

  const data = await res.json();
  const todayStr = new Date().toISOString().split("T")[0];
  const todayDay = data.data?.schedule?.find(d => d.date === todayStr);

  let html = `<div class="section-label">📅 ${data.name || "Today"}</div>`;

  if (!todayDay || !todayDay.blocks?.length) {
    html += `<div class="empty">No blocks scheduled for today.</div>`;
    content.innerHTML = html;
    return;
  }

  html += `
    <div class="stats-row">
      <div class="stat-box">
        <div class="stat-num">${todayDay.blocks.filter(b => !b.is_break).length}</div>
        <div class="stat-lbl">Blocks</div>
      </div>
      <div class="stat-box">
        <div class="stat-num">${todayDay.total_hours || "—"}h</div>
        <div class="stat-lbl">Total</div>
      </div>
      <div class="stat-box">
        <div class="stat-num" style="font-size:0.75rem;padding-top:4px;color:#64748b;text-transform:capitalize;">${todayDay.workload_level || "—"}</div>
        <div class="stat-lbl">Load</div>
      </div>
    </div>
  `;

  todayDay.blocks.forEach(block => {
    if (block.is_break) {
      html += `
        <div class="block-card" style="opacity:0.6;border-left-color:#cbd5e1;">
          <div class="block-time">${block.time_slot?.split(" - ")[0] || ""}</div>
          <div class="block-info">
            <div class="block-title">☕ Break</div>
          </div>
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
  const res = await apiFetch("/grades/data", { method: "GET" });
  if (!res.ok) {
    content.innerHTML = notConnected();
    return;
  }

  const data = await res.json();
  if (!Array.isArray(data) || !data.length) {
    content.innerHTML = `<div class="empty">No grades available yet.</div>`;
    return;
  }

  let html = `<div class="section-label">📊 Current Grades</div>`;
  data.forEach(g => {
    const colors = gradeColor(g.letter);
    const bar = Math.min(g.percentage || 0, 100);
    html += `
      <div class="grade-row" style="background:white;border:1px solid #e2eaf5;border-radius:10px;padding:10px 12px;margin-bottom:6px;display:flex;align-items:center;gap:10px;">
        <div style="flex:1;min-width:0;">
          <div class="grade-name" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:0.8rem;font-weight:600;color:#0f172a;">${g.course}</div>
          <div class="grade-bar-wrap" style="height:4px;background:#e2eaf5;border-radius:2px;margin-top:4px;overflow:hidden;">
            <div class="grade-bar" style="width:${bar}%;background:${colors.color};height:100%;border-radius:2px;"></div>
          </div>
          <div style="font-size:0.65rem;color:#94a3b8;margin-top:2px;">${g.percentage !== null && g.percentage !== undefined ? g.percentage + "%" : "N/A"}</div>
        </div>
        <div class="grade-badge" style="background:${colors.bg};color:${colors.color};font-size:0.88rem;font-weight:700;padding:3px 10px;border-radius:8px;">${g.letter}</div>
      </div>`;
  });

  content.innerHTML = html;
}

async function loadTab(tab) {
  currentTab = tab;
  document.querySelectorAll(".tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });

  const content = document.getElementById("content");
  content.innerHTML = `<div class="loading"><div class="spinner"></div>Loading...</div>`;

  if (tab === "tasks") await loadTasks(content);
  else if (tab === "schedule") await loadSchedule(content);
  else if (tab === "grades") await loadGrades(content);
}

async function handleAuthSubmit(event) {
  event.preventDefault();
  setStatus("");

  const name = document.getElementById("nameInput").value.trim();
  const email = document.getElementById("emailInput").value.trim();
  const password = document.getElementById("passwordInput").value;

  if (!email || !password || (authMode === "signup" && !name)) {
    setStatus("Fill out every required field.", "error");
    return;
  }

  const submitBtn = document.getElementById("authSubmitBtn");
  const originalText = submitBtn ? submitBtn.textContent : "";
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = authMode === "login" ? "Signing in..." : "Creating account...";
  }

  try {
    const payload = authMode === "login"
      ? { email, password }
      : { name, email, password };

    const result = await authRequest(authMode === "login" ? "login" : "register", payload);

    await setSession(result.token, result.user);
    setStatus("Signed in.", "ok");
    await showApp();
  } catch (err) {
    setStatus(err.message || "Authentication failed.", "error");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = originalText || (authMode === "login" ? "Login" : "Create account");
    }
  }
}

async function logout() {
  try {
    await apiFetch(AUTH_ENDPOINTS.logout, { method: "POST" });
  } catch (_) {
    // ignore
  } finally {
    await clearSession();
    await showAuth();
  }
}

async function showAuth() {
  document.getElementById("authView").classList.remove("hidden");
  document.getElementById("appView").classList.add("hidden");
  document.getElementById("logoutBtn").classList.add("hidden");
  showAuthMode(authMode);
}

async function showApp() {
  const session = await getSession();
  if (!session.token) {
    await showAuth();
    return;
  }

  document.getElementById("authView").classList.add("hidden");
  document.getElementById("appView").classList.remove("hidden");
  document.getElementById("logoutBtn").classList.remove("hidden");
  await loadTab(currentTab);
}

async function validateSession() {
  const session = await getSession();
  if (!session.token) {
    await showAuth();
    return;
  }

  const res = await apiFetch(AUTH_ENDPOINTS.me, { method: "GET" }).catch(() => null);

  if (res && res.ok) {
    try {
      const data = await res.json();
      if (data?.user) {
        await setStorage({ [STORAGE_KEYS.user]: data.user });
      }
      await showApp();
      return;
    } catch (_) {
      // fall through
    }
  }

  await clearSession();
  await showAuth();
}

document.addEventListener("DOMContentLoaded", () => {
  const openAppBtn = document.getElementById("openAppBtn");
  const logoutBtn = document.getElementById("logoutBtn");
  const loginModeBtn = document.getElementById("loginModeBtn");
  const signupModeBtn = document.getElementById("signupModeBtn");
  const authForm = document.getElementById("authForm");

  if (openAppBtn) openAppBtn.addEventListener("click", openApp);
  if (logoutBtn) logoutBtn.addEventListener("click", logout);

  if (loginModeBtn) loginModeBtn.addEventListener("click", () => showAuthMode("login"));
  if (signupModeBtn) signupModeBtn.addEventListener("click", () => showAuthMode("signup"));
  if (authForm) authForm.addEventListener("submit", handleAuthSubmit);

  document.querySelectorAll(".tab").forEach(btn => {
    btn.addEventListener("click", () => loadTab(btn.dataset.tab));
  });

  validateSession();
});
