/* Next Best Action card.
 *
 * Everything lives inside one IIFE. Nothing is attached to `window` and no
 * identifier here is shared with command_center.js — page scripts in this app
 * share one global scope, and a collision does not fail loudly, it silently
 * kills the rest of the script block.
 *
 * The card is additive by construction: if /api/next-action 404s (the student
 * is outside the rollout) or errors, the card stays hidden and the page is
 * exactly what it was before this file existed.
 */
(function () {
  "use strict";

  var card = document.getElementById("naCard");
  if (!card) return;

  var el = {
    title: document.getElementById("naTitle"),
    meta: document.getElementById("naMeta"),
    why: document.getElementById("naWhy"),
    method: document.getElementById("naMethod"),
    narration: document.getElementById("naNarration"),
    confidence: document.getElementById("naConfidence"),
    start: document.getElementById("naStart"),
    notNow: document.getElementById("naNotNow"),
    whyBtn: document.getElementById("naWhyBtn"),
    breakdown: document.getElementById("naBreakdown"),
    consequence: document.getElementById("naConsequence"),
    consequenceMsg: document.getElementById("naConsequenceMsg"),
    consequenceActions: document.getElementById("naConsequenceActions"),
    upcomingWrap: document.getElementById("naUpcomingWrap"),
    upcoming: document.getElementById("naUpcoming"),
    risk: document.getElementById("naRisk")
  };

  var state = { headline: null, upcoming: [], pendingOverride: null };

  // ── helpers ────────────────────────────────────────────────────────

  function text(node, value) {
    node.textContent = value == null ? "" : String(value);
  }

  function show(node, on) {
    if (on) {
      node.removeAttribute("hidden");
      return;
    }
    // Hiding a node that currently holds focus drops the caret to <body>,
    // which for a keyboard user means losing their place in the page
    // entirely. Move focus somewhere meaningful first.
    if (node.contains(document.activeElement)) {
      var fallback = el.start && !el.start.hasAttribute("hidden") ? el.start : card;
      try { fallback.focus({ preventScroll: true }); } catch (e) { /* older browsers */ }
    }
    node.setAttribute("hidden", "");
  }

  function minutesLabel(mins) {
    var m = Number(mins) || 0;
    if (m < 60) return m + " min";
    var h = Math.floor(m / 60);
    var rest = m % 60;
    return rest ? h + "h " + rest + "m" : h + "h";
  }

  function dueLabel(iso) {
    if (!iso) return "";
    var due = new Date(iso + "T00:00:00");
    if (isNaN(due.getTime())) return "";
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    var days = Math.round((due - today) / 86400000);
    if (days < 0) return "overdue";
    if (days === 0) return "due today";
    if (days === 1) return "due tomorrow";
    if (days < 7) return "due in " + days + " days";
    return "due " + due.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function json(url, options) {
    return fetch(url, Object.assign({ credentials: "same-origin" }, options || {}))
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      });
  }

  function postJSON(url, body, extra) {
    return json(url, Object.assign({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }, extra || {}));
  }

  // ── rendering ──────────────────────────────────────────────────────

  function renderConfidence(action) {
    var pct = Math.round((Number(action.confidence) || 0) * 100);
    // Below 50% we say so in words rather than showing a number that looks
    // more precise than the thing it is measuring.
    if (pct < 50) {
      text(el.confidence, "best guess");
      el.confidence.setAttribute("data-level", "low");
    } else {
      text(el.confidence, pct + "% confident");
      el.confidence.setAttribute("data-level", "normal");
    }
  }

  function renderHeadline(action) {
    text(el.title, action.title);

    var bits = [];
    if (action.course) bits.push(action.course);
    bits.push(minutesLabel(action.minutes));
    var due = dueLabel(action.due_date);
    if (due) bits.push(due);
    text(el.meta, bits.join(" · "));

    el.why.innerHTML = "";
    (action.why || []).slice(0, 4).forEach(function (line) {
      var li = document.createElement("li");
      li.textContent = line;
      el.why.appendChild(li);
    });

    if (action.method) {
      text(el.method, action.method);
      show(el.method, true);
    } else {
      show(el.method, false);
    }

    renderConfidence(action);
    el.start.textContent =
      action.kind === "break" ? "Take the break" :
      action.kind === "continue_task" ? "Keep going" : "Start this";
    // "Not now" on a break is nonsense — the alternative to a break is
    // just carrying on, which is what closing the card already means.
    show(el.notNow, action.kind !== "break");
  }

  function renderBreakdown(action) {
    el.breakdown.innerHTML = "";
    var parts = (action.components || []).filter(function (c) {
      return Math.abs(Number(c.value) || 0) >= 0.005;
    });
    if (!parts.length) {
      el.breakdown.textContent = "No single factor dominated this one.";
      return;
    }
    var peak = parts.reduce(function (max, c) {
      return Math.max(max, Math.abs(Number(c.value) || 0));
    }, 0.0001);

    var labels = {
      deadline: "Deadline pressure",
      academic_value: "Weight in your grade",
      mastery_gap: "How shaky this is",
      learning_gain: "How much you'd learn",
      completion: "Odds you finish it",
      schedule_fit: "Fits your time",
      continuity: "Same subject as now",
      time_cost: "Time it takes",
      stress: "How loaded today is",
      context_switch: "Switching subjects",
      fatigue: "How long you've worked"
    };

    parts.sort(function (a, b) {
      return Math.abs(b.value) - Math.abs(a.value);
    });

    parts.forEach(function (c) {
      var value = Number(c.value) || 0;
      var row = document.createElement("div");
      row.className = "na-bd-row";
      row.setAttribute("data-sign", value < 0 ? "negative" : "positive");

      var name = document.createElement("span");
      name.textContent = labels[c.key] || c.key;

      var val = document.createElement("span");
      val.className = "na-bd-val";
      val.textContent = (value > 0 ? "+" : "") + value.toFixed(2);

      var bar = document.createElement("div");
      bar.className = "na-bd-bar";
      // The number is already in the row as text; the bar restates it
      // visually and has nothing of its own to announce.
      bar.setAttribute("aria-hidden", "true");
      var fill = document.createElement("span");
      fill.style.width = Math.round((Math.abs(value) / peak) * 100) + "%";
      if (value < 0) fill.style.background = "var(--danger, #dc2626)";
      bar.appendChild(fill);

      row.appendChild(name);
      row.appendChild(val);
      el.breakdown.appendChild(row);
      el.breakdown.appendChild(bar);
    });
  }

  function renderUpcoming(list) {
    el.upcoming.innerHTML = "";
    if (!list.length) {
      show(el.upcomingWrap, false);
      return;
    }
    list.slice(0, 3).forEach(function (a) {
      var li = document.createElement("li");
      var strong = document.createElement("b");
      strong.textContent = a.title;
      li.appendChild(strong);
      var tail = " — " + minutesLabel(a.minutes);
      var due = dueLabel(a.due_date);
      if (due) tail += ", " + due;
      li.appendChild(document.createTextNode(tail));
      el.upcoming.appendChild(li);
    });
    show(el.upcomingWrap, true);
  }

  function renderRisk(items) {
    if (!items || !items.length) {
      show(el.risk, false);
      return;
    }
    var top = items[0];
    var when =
      top.days_away < 0 ? "already overdue" :
      top.days_away === 0 ? "due today" : "due in " + top.days_away + " days";
    var line =
      top.kind === "mastery"
        ? "Most at risk: " + top.title + " — assessment in " + top.days_away +
          " days and your grasp of it is still thin."
        : "Most at risk: " + top.title + " (" + when + ").";
    text(el.risk, line);
    show(el.risk, true);
  }

  // ── override consequences ──────────────────────────────────────────

  function renderConsequence(preview) {
    el.consequenceMsg.innerHTML = "";
    el.consequenceActions.innerHTML = "";

    var summary = document.createElement("div");
    summary.textContent = preview.summary || "";
    el.consequenceMsg.appendChild(summary);

    var entries = []
      .concat((preview.gains || []).map(function (g) { return { c: g, good: true }; }))
      .concat((preview.costs || []).map(function (c) { return { c: c, good: false }; }));

    if (entries.length) {
      var list = document.createElement("ul");
      entries.slice(0, 5).forEach(function (entry) {
        var li = document.createElement("li");
        li.className = entry.good ? "na-gain" : "na-cost";
        // The sign is carried in words as well as in the glyph and the
        // colour. A screen reader announcing a bare minus sign, and a
        // colour-blind reader seeing none, both still get "costs you".
        li.textContent =
          (entry.good ? "Gains: " : "Costs: ") + describeDelta(entry.c);
        list.appendChild(li);
      });
      el.consequenceMsg.appendChild(list);
    }

    if (preview.infeasible_reason) {
      var warn = document.createElement("div");
      warn.className = "na-cost na-consequence-warn";
      warn.textContent = preview.infeasible_reason;
      el.consequenceMsg.appendChild(warn);
    }

    var confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "na-btn na-btn-primary";
    confirm.textContent = "Move it anyway";
    confirm.addEventListener("click", applyOverride);

    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "na-btn";
    cancel.textContent = "Keep it today";
    cancel.addEventListener("click", function () {
      state.pendingOverride = null;
      show(el.consequence, false);
      try { el.notNow.focus({ preventScroll: true }); } catch (e) { /* older browsers */ }
    });

    el.consequenceActions.appendChild(confirm);
    el.consequenceActions.appendChild(cancel);
    show(el.consequence, true);

    // A panel of consequences that appears without taking focus is one a
    // keyboard user has to go looking for, and they have no way to know it
    // is there. Land them on the affirmative choice.
    try { confirm.focus({ preventScroll: true }); } catch (e) { /* older browsers */ }
  }

  function dayPhrase(iso) {
    var d = new Date(iso + "T00:00:00");
    if (isNaN(d.getTime())) return "";
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    var days = Math.round((d - today) / 86400000);
    if (days === 0) return "today";
    if (days === 1) return "tomorrow";
    if (days === -1) return "yesterday";
    return "on " + d.toLocaleDateString(undefined, { weekday: "long" });
  }

  function describeDelta(consequence) {
    var key = String(consequence.key || "");
    var delta = Number(consequence.delta) || 0;
    if (key.indexOf("minutes:") === 0) {
      // Phrasing comes from the date in the key, not from surgery on the
      // label. Stripping " workload" off "Today's workload" and lowercasing
      // it produced "45 min less on today's" — a dangling possessive.
      var when = dayPhrase(key.slice("minutes:".length));
      var direction = delta > 0 ? " more" : " less";
      return minutesLabel(Math.abs(delta)) + direction + (when ? " " + when : "");
    }
    if (key === "deferred") {
      return minutesLabel(Math.abs(delta)) + " of work with nowhere left to go";
    }
    // Metric deltas are 0..1; a percentage-point phrasing is the only one a
    // student can act on, and it is honest because the metric is a ratio.
    return Math.round(Math.abs(delta) * 100) + " points of " + consequence.label;
  }

  function todayISO() {
    var d = new Date();
    return d.getFullYear() + "-" +
      String(d.getMonth() + 1).padStart(2, "0") + "-" +
      String(d.getDate()).padStart(2, "0");
  }

  function previewOverride() {
    if (!state.headline || !state.headline.task_id) return;
    var body = {
      action: "move",
      task_id: state.headline.task_id,
      day: todayISO()
    };
    state.pendingOverride = body;
    el.notNow.disabled = true;
    postJSON("/api/schedule/override", body)
      .then(function (data) {
        if (!data.changed) {
          text(el.consequence, data.summary || "Nothing would change.");
          show(el.consequence, true);
          state.pendingOverride = null;
          return;
        }
        renderConsequence(data);
      })
      .catch(function () {
        text(el.consequence, "Could not work out what that would cost. Nothing was changed.");
        show(el.consequence, true);
        state.pendingOverride = null;
      })
      .finally(function () {
        el.notNow.disabled = false;
      });
  }

  function applyOverride() {
    if (!state.pendingOverride) return;
    var body = state.pendingOverride;
    var offer = "";
    postJSON("/api/schedule/override/apply", body)
      .then(function (data) {
        offer = (data && data.rebalance_offer) || "";
        return postJSON("/api/next-action/respond", {
          task_id: body.task_id,
          accepted: false
        });
      })
      .catch(function () { /* the plan write is the part that matters */ })
      .finally(function () {
        state.pendingOverride = null;
        load();
        // An override is applied literally, which is what the student asked
        // for and can sometimes leave a day that no longer works. Saying
        // nothing about that is how the plan quietly becomes wrong; saying
        // it every time would train them to dismiss it. The server decides
        // whether it is worth raising.
        if (offer) renderRebalanceOffer(offer);
        else show(el.consequence, false);
      });
  }

  function renderRebalanceOffer(message) {
    el.consequenceMsg.innerHTML = "";
    el.consequenceActions.innerHTML = "";

    var line = document.createElement("div");
    line.textContent = message;
    el.consequenceMsg.appendChild(line);

    var rebalance = document.createElement("a");
    rebalance.className = "na-btn na-btn-primary";
    rebalance.href = "/scheduler";
    rebalance.textContent = "Rebalance my week";

    var dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "na-btn";
    dismiss.textContent = "Leave it";
    dismiss.addEventListener("click", function () {
      show(el.consequence, false);
    });

    el.consequenceActions.appendChild(rebalance);
    el.consequenceActions.appendChild(dismiss);
    show(el.consequence, true);
    try { rebalance.focus({ preventScroll: true }); } catch (e) { /* older browsers */ }
  }

  // ── wiring ─────────────────────────────────────────────────────────

  el.whyBtn.addEventListener("click", function () {
    var open = el.breakdown.hasAttribute("hidden");
    show(el.breakdown, open);
    el.whyBtn.setAttribute("aria-expanded", open ? "true" : "false");
  });

  el.notNow.addEventListener("click", previewOverride);

  el.start.addEventListener("click", function () {
    if (!state.headline) return;
    var action = state.headline;
    if (action.task_id) {
      // `keepalive` matters here: the next statement navigates away, and a
      // normal fetch is cancelled when the document unloads. Without it the
      // *accepted* half of the feedback loop is dropped almost every time,
      // while rejections — which never navigate — record fine. That skew
      // would quietly teach the model that nobody takes its advice.
      postJSON("/api/next-action/respond", {
        task_id: action.task_id,
        accepted: true
      }, { keepalive: true })
        .catch(function () { /* telemetry must never block the student */ });
    }
    if (action.kind === "break") return;
    // Hand off to the Active-study surface, which is where a sitting is
    // actually run. Deep-linking by block keeps the timer, the focus
    // check-ins, and the feedback loop all pointing at the same work.
    var target = "/active";
    if (action.block_id) target += "?block=" + encodeURIComponent(action.block_id);
    else if (action.task_id) target += "?task=" + encodeURIComponent(action.task_id);
    window.location.href = target;
  });

  // ── load ───────────────────────────────────────────────────────────

  function loadNarration() {
    json("/api/next-action/explain")
      .then(function (data) {
        var exp = data && data.explanation;
        if (!exp || !exp.why) return;
        var line = exp.why;
        if (exp.uncertainty) line += " (Less certain: " + exp.uncertainty + ".)";
        text(el.narration, line);
        show(el.narration, true);
      })
      .catch(function () { /* the grounded reason list is already on screen */ });
  }

  function load() {
    json("/api/next-action?limit=4")
      .then(function (data) {
        if (!data || !data.has_recommendation || !data.headline) {
          show(card, false);
          return;
        }
        state.headline = data.headline;
        state.upcoming = data.upcoming || [];
        renderHeadline(data.headline);
        renderBreakdown(data.headline);
        renderUpcoming(state.upcoming);
        renderRisk(data.at_risk);
        show(el.breakdown, false);
        el.whyBtn.setAttribute("aria-expanded", "false");
        show(el.consequence, false);
        show(card, true);
        loadNarration();
      })
      .catch(function () {
        // 404 outside the rollout, or any other failure. Either way the page
        // renders as it did before this card existed.
        show(card, false);
      });
  }

  load();
})();
