// Focus Shield block page. External script: MV3 extension pages refuse
// inline <script>. Talks only to background.js, which owns the state.

const params = new URLSearchParams(location.search);
const original = params.get("u") || "";
let endsAt = 0;
let timer = null;

function $(id) { return document.getElementById(id); }

function send(type) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type }, (res) => resolve(res || null));
    } catch (_) {
      resolve(null);
    }
  });
}

function safeOriginal() {
  // Only ever send the tab back to an http(s) address.
  try {
    const url = new URL(original);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
  } catch (_) {
    return "";
  }
}

function fmt(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function tick() {
  const left = endsAt - Date.now();
  $("timeLeft").textContent = fmt(left);
  if (left <= 0) {
    clearInterval(timer);
    // The block is over: re-check, and go back to where they were headed.
    send("focus_shield_refresh").then(() => render());
  }
}

function goBack() {
  const target = safeOriginal();
  if (target) location.replace(target);
  else $("status").textContent = "You're free to browse.";
}

async function render() {
  const s = await send("focus_shield_status");
  if (!s || !s.blocking || !s.block) {
    goBack();
    return;
  }
  const b = s.block;
  $("kicker").textContent = b.source === "active" ? "Active session running"
    : b.source === "manual" ? "Focus session running" : "Planned study block";
  $("title").textContent = b.title || "Study block";
  $("course").textContent = b.course || "";
  try { $("site").textContent = original ? `${new URL(original).hostname} is paused until this block ends.` : ""; } catch (_) {}
  endsAt = Date.parse(b.ends_at) || Date.now();
  const left = s.breaks_left || 0;
  $("breakBtn").disabled = left <= 0;
  $("breakBtn").textContent = left > 0
    ? `Take a ${s.break_minutes || 5}-minute break (${left} left)`
    : "No breaks left in this block";
  if (s.offline_since) $("status").textContent = "Offline — using your last synced plan.";
  clearInterval(timer);
  tick();
  timer = setInterval(tick, 1000);
}

$("breakBtn").addEventListener("click", async () => {
  $("breakBtn").disabled = true;
  const res = await send("focus_shield_break");
  if (res && res.ok) {
    $("status").textContent = "Break started. Blocking resumes in 5 minutes.";
    setTimeout(goBack, 600);
  } else {
    $("status").textContent = (res && res.message) || "No breaks left in this block.";
    render();
  }
});

$("doneBtn").addEventListener("click", async () => {
  $("doneBtn").disabled = true;
  await send("focus_shield_done");
  $("status").textContent = "Nice work. Blocking is off for the rest of this block.";
  setTimeout(goBack, 600);
});

render();
