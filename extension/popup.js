const BASE_URL = "https://intelliplan.up.railway.app";
let currentTab = "tasks";

function openApp() {
  chrome.tabs.create({ url: BASE_URL + "/dashboard" });
}

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll(".tab").forEach((t, i) => {
    t.classList.toggle("active", ["tasks", "schedule", "grades"][i] === tab);
  });
  loadTab(tab);
}

async function fetchFromApp(endpoint) {
  try {
    const res = await fetch(BASE_URL + endpoint, {
      credentials: "include",
      headers: { "Content-Type": "application/json" }
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    return null;
  }
}

async function loadTab(tab) {
  const content = document.getElementById("content");
  content.innerHTML = `<div class="loading"><div class="spinner"></div>Loading...</div>`;

  if (tab === "tasks") await loadTasks(content);
  else if (tab === "schedule") await loadSchedule(content);
  else if (tab === "grades") await loadGrades(content);
}

async function loadTasks(content) {
  const data = await fetchFromApp("/tasks/unified");
  if (!data) {
    content.innerHTML = notConnected();
    return;
  }

  const all = [
    ...data.overdue.map(t => ({ ...t, _bucket: "overdue" })),
    ...data.today.map(t => ({ ...t, _bucket: "today" })),
    ...data.upcoming.map(t => ({ ...t, _bucket: "upcoming" }))
  ];

  if (!all.length) {
    content.innerHTML = `<div class="empty">✅ All clear — no tasks!</div>`;
    return;
  }

  const overdue = data.overdue.length;
  const today = data.today.length;
  const upcoming = data.upcoming.length;

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
    </div>
  `;

  if (data.overdue.length) {
    html += `<div class="section-label">⚠ Overdue</div>`;
    html += data.overdue.map(t => taskCard(t, "high")).join("");
  }

  if (data.today.length) {
    html += `<div class="section-label">📅 Due Today</div>`;
    html += data.today.map(t => taskCard(t, t.priority?.toLowerCase() || "medium")).join("");
  }

  if (data.upcoming.length) {
    html += `<div class="section-label">🗓 Upcoming</div>`;
    html += data.upcoming.slice(0, 5).map(t => taskCard(t, t.priority?.toLowerCase() || "low")).join("");
    if (data.upcoming.length > 5) {
      html += `<div style="text-align:center;font-size:0.75rem;color:#94a3b8;padding:8px;">+${data.upcoming.length - 5} more — <a href="#" onclick="openApp()" style="color:#3b82f6;">open app</a></div>`;
    }
  }

  content.innerHTML = html;
}

async function loadSchedule(content) {
  const data = await fetchFromApp("/schedule/saved");
  if (!data || data.status !== "ok") {
    content.innerHTML = `
      <div class="empty">
        <div style="font-size:1.5rem;margin-bottom:8px;">📅</div>
        No saved schedule.<br>
        <a href="#" onclick="chrome.tabs.create({url:'${BASE_URL}/scheduler'})" style="color:#3b82f6;font-size:0.82rem;">Generate one in IntelliPlan ↗</a>
      </div>`;
    return;
  }

  const todayStr = new Date().toISOString().split("T")[0];
  const todayDay = data.data?.schedule?.find(d => d.date === todayStr);

  let html = `<div class="section-label">📅 ${data.name}</div>`;

  if (!todayDay || !todayDay.blocks?.length) {
    html += `<div class="empty">No blocks scheduled for today.</div>`;
    content.innerHTML = html;
    return;
  }

  const studyBlocks = todayDay.blocks.filter(b => !b.is_break);
  const breakBlocks = todayDay.blocks.filter(b => b.is_break);

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
  const data = await fetchFromApp("/grades/data");
  if (!data) {
    content.innerHTML = notConnected();
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
          <div class="grade-name" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${g.course}</div>
          <div class="grade-bar-wrap">
            <div class="grade-bar" style="width:${bar}%;background:${colors.color};"></div>
          </div>
          <div style="font-size:0.65rem;color:#94a3b8;margin-top:2px;">${g.percentage !== null ? g.percentage + "%" : "N/A"}</div>
        </div>
        <div class="grade-badge" style="background:${colors.bg};color:${colors.color};">${g.letter}</div>
      </div>`;
  });

  content.innerHTML = html;
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

function gradeColor(letter) {
  if (!letter) return { bg: "#f1f5f9", color: "#64748b" };
  if (letter.startsWith("A")) return { bg: "#f0fdf4", color: "#22c55e" };
  if (letter.startsWith("B")) return { bg: "#dbeafe", color: "#3b82f6" };
  if (letter.startsWith("C")) return { bg: "#fffbeb", color: "#f59e0b" };
  return { bg: "#fef2f2", color: "#ef4444" };
}

function notConnected() {
  return `
    <div class="not-connected">
      <div style="font-size:2rem;margin-bottom:12px;">🔗</div>
      <p>You need to be logged in to IntelliPlan to see your data.</p>
      <button class="connect-btn" onclick="chrome.tabs.create({url:'${BASE_URL}/login'})">
        Sign in to IntelliPlan
      </button>
    </div>`;
}

// Init
loadTab("tasks");