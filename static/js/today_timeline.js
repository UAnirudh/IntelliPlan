/* Today timeline — the day, drawn to scale.
 *
 * One IIFE, nothing on `window`: page scripts here share a global scope and
 * a name collision silently kills the rest of a script block.
 *
 * Classes (from the timetable), study blocks (from the saved plan) and
 * calendar busy time come from GET /api/today/timeline. A study block can be
 * moved by dragging it (mouse anywhere on it; touch on its grip, so the page
 * still scrolls) or from the keyboard (arrow keys move it 15 minutes, Enter
 * asks what the move would do). Every move is priced first with
 * {preview: true}, shown to the student, and only committed when they say
 * so — the same "show the consequence, then apply" contract as the rest of
 * the scheduler.
 *
 * Additive by construction: a 404 (flag off), 401 or error leaves the card
 * hidden and the page exactly as it was.
 */
(function () {
  "use strict";

  var card = document.getElementById("tlCard");
  if (!card) return;

  var el = {
    grid: document.getElementById("tlGrid"),
    scroll: document.getElementById("tlScroll"),
    rotation: document.getElementById("tlRotation"),
    status: document.getElementById("tlStatus"),
    empty: document.getElementById("tlEmpty"),
    confirm: document.getElementById("tlConfirm"),
    confirmMsg: document.getElementById("tlConfirmMsg"),
    confirmYes: document.getElementById("tlConfirmYes"),
    confirmNo: document.getElementById("tlConfirmNo"),
    next: document.getElementById("tlNext")
  };

  var PX_PER_MIN = 1.1;
  var SNAP = 5;
  var KEY_STEP = 15;
  var state = { data: null, pending: null, drag: null, nowTimer: null, focusId: null };

  // ── helpers ────────────────────────────────────────────────────────

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function localDate() {
    var d = new Date();
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  function nowMinutes() {
    var d = new Date();
    return d.getHours() * 60 + d.getMinutes();
  }

  function hhmm(min) {
    min = Math.max(0, Math.min(24 * 60 - 1, Math.round(min)));
    return pad(Math.floor(min / 60)) + ":" + pad(min % 60);
  }

  function fmt12(min) {
    var h = Math.floor(min / 60) % 24, m = Math.round(min % 60);
    return (h % 12 || 12) + ":" + pad(m) + " " + (h < 12 ? "AM" : "PM");
  }

  function say(message) {
    el.status.textContent = message || "";
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        return { ok: r.ok, status: r.status, data: d };
      });
    });
  }

  function y(min) { return (min - state.data.range.start) * PX_PER_MIN; }

  function isToday() { return state.data && state.data.date === localDate(); }

  // ── layout ─────────────────────────────────────────────────────────

  // Greedy column assignment within clusters of overlapping items — the
  // calendar-app layout, so two things at once sit side by side rather than
  // one hiding the other.
  function columns(items) {
    var sorted = items.slice().sort(function (a, b) {
      return a.start_minute - b.start_minute || b.end_minute - a.end_minute;
    });
    var cluster = [], clusterEnd = -1, out = [];
    function flush() {
      var cols = [];
      cluster.forEach(function (it) {
        var c = 0;
        while (cols[c] !== undefined && cols[c] > it.start_minute) c++;
        cols[c] = it.end_minute;
        it._col = c;
      });
      cluster.forEach(function (it) { it._cols = cols.length; out.push(it); });
      cluster = [];
    }
    sorted.forEach(function (it) {
      if (cluster.length && it.start_minute >= clusterEnd) { flush(); clusterEnd = -1; }
      cluster.push(it);
      clusterEnd = Math.max(clusterEnd, it.end_minute);
    });
    if (cluster.length) flush();
    return out;
  }

  function describe(item) {
    var kind = { "class": "Class", study: "Study block", "break": "Break", busy: "Busy" }[item.kind] || "";
    var bits = [kind + ": " + item.title, item.label];
    if (item.subtitle && item.kind !== "busy") bits.push(item.subtitle);
    if (item.done) bits.push("done");
    return bits.join(", ");
  }

  function render() {
    var data = state.data;
    var grid = el.grid;
    grid.innerHTML = "";
    var height = (data.range.end - data.range.start) * PX_PER_MIN;
    grid.style.height = height + "px";

    for (var h = data.range.start; h <= data.range.end; h += 60) {
      var line = document.createElement("div");
      line.className = "tl-hour";
      line.style.top = y(h) + "px";
      line.setAttribute("aria-hidden", "true");
      var lab = document.createElement("span");
      lab.textContent = fmt12(h).replace(":00", "");
      line.appendChild(lab);
      grid.appendChild(line);
    }

    var allDay = data.items.filter(function (i) {
      return i.kind === "busy" && i.start_minute === 0 && i.end_minute >= 1440;
    });
    var timed = data.items.filter(function (i) { return allDay.indexOf(i) === -1; });

    var list = document.createElement("ul");
    list.className = "tl-items";
    list.setAttribute("aria-label", "Your day, in time order");
    columns(timed).forEach(function (item) {
      var li = document.createElement("li");
      li.className = "tl-item tl-" + item.kind + (item.done ? " is-done" : "") + (item.conflict ? " has-conflict" : "");
      li.dataset.id = item.id;
      li.style.top = y(item.start_minute) + "px";
      li.style.height = Math.max(18, (item.end_minute - item.start_minute) * PX_PER_MIN - 2) + "px";
      li.style.left = "calc(" + (item._col / item._cols * 100) + "% + 2px)";
      li.style.width = "calc(" + (100 / item._cols) + "% - 4px)";
      if (item.color && item.kind === "class") li.style.setProperty("--tl-swatch", item.color);

      var body = document.createElement("div");
      body.className = "tl-body";
      var title = document.createElement("span");
      title.className = "tl-title";
      title.textContent = item.title;
      var meta = document.createElement("span");
      meta.className = "tl-meta";
      meta.textContent = item.label + (item.subtitle && item.kind !== "busy" ? " · " + item.subtitle : "");
      body.appendChild(title);
      body.appendChild(meta);
      li.appendChild(body);

      if (item.movable) {
        li.tabIndex = 0;
        li.setAttribute("aria-roledescription", "movable study block");
        li.setAttribute("aria-label", describe(item) +
          ". Arrow up or down moves it 15 minutes; Enter checks the new time.");
        var grip = document.createElement("span");
        grip.className = "tl-grip";
        grip.setAttribute("aria-hidden", "true");
        li.appendChild(grip);
        li.addEventListener("pointerdown", onPointerDown);
        li.addEventListener("keydown", onKey);
      } else {
        li.setAttribute("aria-label", describe(item));
      }
      list.appendChild(li);
    });
    grid.appendChild(list);

    var nowLine = document.createElement("div");
    nowLine.className = "tl-now";
    nowLine.id = "tlNowLine";
    nowLine.setAttribute("aria-hidden", "true");
    grid.appendChild(nowLine);

    var labels = [];
    if (data.rotation_label) labels.push(data.rotation_label);
    if (!data.school_day && data.skip_reason && data.skip_reason !== "weekend") labels.push("No school: " + data.skip_reason);
    allDay.forEach(function () { labels.push("Busy all day"); });
    el.rotation.textContent = labels.join(" · ");

    var nothing = !data.items.length;
    el.empty.hidden = !nothing;
    el.scroll.hidden = nothing;

    if (el.next) {
      var n = data.next_class;
      el.next.textContent = n
        ? "Next class: " + n.title + (n.room ? " in " + n.room : "") + " at " + fmt12(n.start_minute) +
          (n.date !== data.date ? " (" + new Date(n.date + "T00:00").toLocaleDateString(undefined, { weekday: "long" }) + ")" : "")
        : "";
      el.next.hidden = !n;
    }
    tickNow(true);
    if (state.focusId) {
      var again = grid.querySelector('[data-id="' + cssEscape(state.focusId) + '"]');
      if (again) again.focus({ preventScroll: true });
      state.focusId = null;
    }
  }

  function cssEscape(v) {
    return window.CSS && CSS.escape ? CSS.escape(v) : String(v).replace(/["\\]/g, "\\$&");
  }

  // The live "now" line, and time left on whatever block is running.
  function tickNow(scrollIntoView) {
    var line = document.getElementById("tlNowLine");
    if (!line || !state.data) return;
    var now = nowMinutes();
    var inRange = isToday() && now >= state.data.range.start && now <= state.data.range.end;
    line.hidden = !inRange;
    if (inRange) line.style.top = y(now) + "px";
    Array.prototype.forEach.call(el.grid.querySelectorAll(".tl-left"), function (n) { n.remove(); });
    Array.prototype.forEach.call(el.grid.querySelectorAll(".is-current"), function (n) { n.classList.remove("is-current"); });
    if (inRange) {
      state.data.items.forEach(function (item) {
        if (item.kind === "busy" || now < item.start_minute || now >= item.end_minute) return;
        var node = el.grid.querySelector('[data-id="' + cssEscape(item.id) + '"]');
        if (!node) return;
        node.classList.add("is-current");
        var span = item.end_minute - item.start_minute;
        var left = item.end_minute - now;
        var ring = document.createElement("span");
        ring.className = "tl-left";
        ring.style.setProperty("--tl-p", String(Math.max(0, Math.min(1, (span - left) / span))));
        ring.textContent = left + " min left";
        node.appendChild(ring);
      });
      if (scrollIntoView && el.scroll.scrollHeight > el.scroll.clientHeight) {
        el.scroll.scrollTop = Math.max(0, y(now) - el.scroll.clientHeight / 3);
      }
    }
  }

  // ── moving ─────────────────────────────────────────────────────────

  function itemById(id) {
    var found = null;
    (state.data.items || []).forEach(function (i) { if (i.id === id) found = i; });
    return found;
  }

  function placeGhost(node, item, start) {
    node.style.top = y(start) + "px";
    node.classList.add("is-moving");
    var meta = node.querySelector(".tl-meta");
    var dur = item.end_minute - item.start_minute;
    if (meta) meta.textContent = fmt12(start) + " – " + fmt12(start + dur);
  }

  function snapBack() {
    state.pending = null;
    el.confirm.hidden = true;
    render();
  }

  function onPointerDown(ev) {
    var node = ev.currentTarget;
    if (state.pending) return;
    // Touch drags only from the grip, so a finger on a block still scrolls.
    if (ev.pointerType !== "mouse" && !ev.target.classList.contains("tl-grip")) return;
    if (ev.button !== undefined && ev.button !== 0) return;
    var item = itemById(node.dataset.id);
    if (!item) return;
    ev.preventDefault();
    node.setPointerCapture(ev.pointerId);
    state.drag = { node: node, item: item, startY: ev.clientY, start: item.start_minute, moved: false };
    node.addEventListener("pointermove", onPointerMove);
    node.addEventListener("pointerup", onPointerUp);
    node.addEventListener("pointercancel", onPointerCancel);
  }

  function dragStart(ev) {
    var d = state.drag;
    var delta = (ev.clientY - d.startY) / PX_PER_MIN;
    var dur = d.item.end_minute - d.item.start_minute;
    var start = Math.round((d.item.start_minute + delta) / SNAP) * SNAP;
    return Math.max(state.data.range.start, Math.min(state.data.range.end - dur, start));
  }

  function onPointerMove(ev) {
    var d = state.drag;
    if (!d) return;
    if (Math.abs(ev.clientY - d.startY) > 3) d.moved = true;
    d.start = dragStart(ev);
    placeGhost(d.node, d.item, d.start);
  }

  function endDrag() {
    var d = state.drag;
    if (!d) return null;
    d.node.removeEventListener("pointermove", onPointerMove);
    d.node.removeEventListener("pointerup", onPointerUp);
    d.node.removeEventListener("pointercancel", onPointerCancel);
    state.drag = null;
    return d;
  }

  function onPointerUp(ev) {
    var d = endDrag();
    if (!d) return;
    if (!d.moved || d.start === d.item.start_minute) { render(); return; }
    preview(d.item, d.start);
  }

  function onPointerCancel() { endDrag(); render(); }

  function onKey(ev) {
    var node = ev.currentTarget;
    var item = itemById(node.dataset.id);
    if (!item) return;
    if (state.pending) return; // a move is already waiting on the confirm panel
    var current = node._kbStart != null ? node._kbStart : item.start_minute;
    var dur = item.end_minute - item.start_minute;
    if (ev.key === "ArrowUp" || ev.key === "ArrowDown") {
      ev.preventDefault();
      var step = (ev.key === "ArrowUp" ? -1 : 1) * (ev.shiftKey ? 60 : KEY_STEP);
      var next = Math.max(state.data.range.start, Math.min(state.data.range.end - dur, current + step));
      node._kbStart = next;
      placeGhost(node, item, next);
      say(item.title + ": " + fmt12(next) + " to " + fmt12(next + dur) + ". Press Enter to check this time, Escape to cancel.");
    } else if (ev.key === "Enter" || ev.key === " ") {
      if (node._kbStart != null && node._kbStart !== item.start_minute) {
        ev.preventDefault();
        var start = node._kbStart;
        node._kbStart = null;
        preview(item, start);
      }
    } else if (ev.key === "Escape") {
      if (node._kbStart != null) {
        node._kbStart = null;
        say("Move cancelled.");
        state.focusId = item.id;
        render();
      }
    }
  }

  function preview(item, start) {
    state.pending = { item: item, start: start, checked: false };
    say("Checking " + fmt12(start) + "…");
    postJSON("/api/today/timeline/move", {
      block_id: item.id, date: state.data.date, start: hhmm(start), preview: true
    }).then(function (res) {
      if (!state.pending || state.pending.item.id !== item.id) return;
      state.pending.checked = true;
      var d = res.data || {};
      if (!res.ok || d.status !== "ok") {
        say(d.message || "Couldn't check that move.");
        state.focusId = item.id;
        snapBack();
        return;
      }
      state.pending.allowed = !!d.allowed;
      el.confirmMsg.textContent = d.summary || "";
      el.confirmYes.hidden = !d.allowed;
      el.confirmNo.textContent = d.allowed ? "Cancel" : "OK";
      el.confirm.hidden = false;
      say(d.summary || "");
      (d.allowed ? el.confirmYes : el.confirmNo).focus();
    }).catch(function () {
      say("You're offline — the move wasn't saved.");
      state.focusId = item.id;
      snapBack();
    });
  }

  function commit() {
    var p = state.pending;
    if (!p || !p.allowed) return snapBack();
    el.confirmYes.disabled = true;
    postJSON("/api/today/timeline/move", {
      block_id: p.item.id, date: state.data.date, start: hhmm(p.start), preview: false, now: hhmm(nowMinutes())
    }).then(function (res) {
      el.confirmYes.disabled = false;
      var d = res.data || {};
      state.pending = null;
      el.confirm.hidden = true;
      state.focusId = p.item.id;
      if (res.ok && d.saved && d.timeline) {
        state.data = d.timeline;
        say("Moved " + p.item.title + " to " + fmt12(p.start) + ".");
        render();
        try { document.dispatchEvent(new CustomEvent("ip:plan-changed", { detail: { source: "timeline" } })); } catch (e) { /* old browsers */ }
      } else {
        say(d.message || d.summary || "Couldn't move that block.");
        render();
      }
    }).catch(function () {
      el.confirmYes.disabled = false;
      say("You're offline — the move wasn't saved.");
      state.focusId = p.item.id;
      snapBack();
    });
  }

  el.confirmYes.addEventListener("click", commit);
  el.confirmNo.addEventListener("click", function () {
    var id = state.pending && state.pending.item.id;
    say(state.pending && state.pending.allowed ? "Move cancelled." : "");
    state.focusId = id;
    snapBack();
  });

  // ── load ───────────────────────────────────────────────────────────

  function load() {
    var url = "/api/today/timeline?date=" + encodeURIComponent(localDate()) +
      "&now=" + encodeURIComponent(hhmm(nowMinutes()));
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || d.status !== "ok") return;
        state.data = d;
        card.hidden = false;
        render();
        if (!state.nowTimer) {
          state.nowTimer = setInterval(function () {
            // Past midnight the card is showing yesterday.
            if (state.data && state.data.date !== localDate()) { load(); return; }
            tickNow(false);
          }, 60 * 1000);
        }
      })
      .catch(function () { /* card stays hidden */ });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && !state.drag && !state.pending) load();
  });
  load();
})();
