/* Timetable editor on /classes.
 *
 * One IIFE, nothing on `window` (page scripts share a global scope). Talks to
 * /api/timetable*: import from StudentVUE / Schoology, read a photo into
 * draft classes the student checks before saving, edit classes inline, and
 * set the rotation (A/B, N-day cycle, Week 1/2), school days and no-school
 * days. Signed-out or flag-off (401/404) hides the panel.
 */
(function () {
  "use strict";

  var panel = document.getElementById("timetable");
  if (!panel) return;

  var WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  var $ = function (id) { return document.getElementById(id); };
  var el = {
    status: $("ttStatus"), list: $("ttList"), today: $("ttToday"),
    importBtn: $("ttImport"), photo: $("ttPhoto"),
    addToggle: $("ttAddToggle"), addForm: $("ttAddForm"), addDays: $("ttNewDays"), addRot: $("ttNewRot"),
    draftsWrap: $("ttDraftsWrap"), drafts: $("ttDrafts"), draftsSave: $("ttDraftsSave"), draftsDiscard: $("ttDraftsDiscard"),
    kind: $("ttKind"), length: $("ttLength"), lengthWrap: $("ttLengthWrap"),
    todayIs: $("ttTodayIs"), todayWrap: $("ttTodayWrap"), rotationSave: $("ttRotationSave"),
    schoolDays: $("ttSchoolDays"),
    skipStart: $("ttSkipStart"), skipEnd: $("ttSkipEnd"), skipLabel: $("ttSkipLabel"), skipAdd: $("ttSkipAdd"),
    skips: $("ttSkips")
  };
  var state = { data: null, drafts: [] };

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function localDate() {
    var d = new Date();
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  function say(message, isError) {
    el.status.textContent = message || "";
    el.status.classList.toggle("is-error", !!isError);
  }

  function call(method, url, body) {
    var opts = { method: method, credentials: "same-origin", headers: {} };
    if (body instanceof FormData) {
      opts.body = body;
    } else if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        return { ok: r.ok, status: r.status, data: d };
      });
    });
  }

  function node(tag, attrs, text) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "className") n.className = attrs[k];
      else n.setAttribute(k, attrs[k]);
    });
    if (text != null) n.textContent = text;
    return n;
  }

  function rotationLabels(settings) {
    var s = settings || (state.data && state.data.settings) || { kind: "none" };
    if (s.kind === "none") return [];
    var n = s.kind === "ab" ? 2 : (s.length || 2);
    var out = [];
    for (var i = 1; i <= n; i++) {
      out.push(s.kind === "ab" ? "AB".charAt(i - 1) : (s.kind === "week" ? "Wk " + i : "Day " + i));
    }
    return out;
  }

  // Checkbox chips. `name` must be unique per group for the browser to keep
  // the labels straight; values are what the API expects.
  function chips(legendText, values, labels, checked, name) {
    var fs = node("fieldset", { className: "tt-days" });
    fs.appendChild(node("legend", null, legendText));
    values.forEach(function (v, i) {
      var lab = node("label", { className: "tt-chip" });
      var input = node("input", { type: "checkbox", value: String(v), name: name });
      input.checked = checked.indexOf(v) !== -1 || checked.indexOf(String(v)) !== -1;
      lab.appendChild(input);
      lab.appendChild(node("span", null, labels[i]));
      fs.appendChild(lab);
    });
    return fs;
  }

  function checkedValues(root, name) {
    return Array.prototype.map.call(
      root.querySelectorAll('input[name="' + name + '"]:checked'),
      function (i) { return i.value; }
    );
  }

  function field(labelText, input, extra) {
    var lab = node("label", { className: "tt-field" + (extra ? " " + extra : "") });
    lab.appendChild(document.createTextNode(labelText));
    lab.appendChild(input);
    return lab;
  }

  var uid = 0;

  function classEditor(cls, draft) {
    uid += 1;
    var key = "tt" + uid;
    var li = node("li", { className: "tt-class" + (draft ? " is-draft" : "") });
    var row = node("div", { className: "tt-row" });
    var inputs = {
      course: node("input", { type: "text", maxlength: "256", value: cls.course || "", required: "required" }),
      period: node("input", { type: "text", maxlength: "32", value: cls.period || "" }),
      room: node("input", { type: "text", maxlength: "64", value: cls.room || "" }),
      teacher: node("input", { type: "text", maxlength: "128", value: cls.teacher || "" }),
      start: node("input", { type: "time", value: cls.start || "" }),
      end: node("input", { type: "time", value: cls.end || "" })
    };
    row.appendChild(field("Class", inputs.course, "is-wide"));
    row.appendChild(field("Period", inputs.period, "is-narrow"));
    row.appendChild(field("Room", inputs.room, "is-narrow"));
    row.appendChild(field("Teacher", inputs.teacher));
    row.appendChild(field("Starts", inputs.start));
    row.appendChild(field("Ends", inputs.end));
    li.appendChild(row);

    var dayRow = node("div", { className: "tt-row" });
    dayRow.style.marginTop = "8px";
    dayRow.appendChild(chips("Meets on (none ticked = every school day)", WEEKDAYS, WEEKDAYS, cls.weekdays || [], key + "d"));
    var rot = rotationLabels();
    if (rot.length) {
      var nums = rot.map(function (_, i) { return i + 1; });
      dayRow.appendChild(chips("Rotation days (none = all)", nums, rot, cls.rotation_days || [], key + "r"));
    }
    li.appendChild(dayRow);

    var foot = node("div", { className: "tt-class-foot" });
    if (cls.source && cls.source !== "manual") {
      foot.appendChild(node("span", { className: "tt-badge" }, "From " + (cls.source === "studentvue" ? "StudentVUE" : cls.source === "schoology" ? "Schoology" : "photo")));
    }
    if (cls.inherits_bell) foot.appendChild(node("span", { className: "tt-warn" }, "Times from your bell schedule."));
    else if (!cls.start) foot.appendChild(node("span", { className: "tt-warn" }, "Add times so your plan works around this class."));
    if (cls.needs_days) foot.appendChild(node("span", { className: "tt-warn" }, "Pick which rotation days this class meets."));

    li._collect = function () {
      return {
        course: inputs.course.value.trim(),
        period: inputs.period.value.trim(),
        room: inputs.room.value.trim(),
        teacher: inputs.teacher.value.trim(),
        start: inputs.start.value,
        end: inputs.end.value,
        weekdays: checkedValues(li, key + "d"),
        rotation_days: checkedValues(li, key + "r").map(Number),
        external_id: cls.external_id || ""
      };
    };
    var name = cls.course || "this class";
    Object.keys(inputs).forEach(function (k) {
      // The visible label says "Room"; a screen reader on a list of ten
      // classes needs to know whose room.
      inputs[k].setAttribute("aria-label", k.charAt(0).toUpperCase() + k.slice(1) + " for " + name);
    });

    if (!draft) {
      var save = node("button", { type: "button", className: "tt-btn tt-btn-primary" }, "Save");
      save.setAttribute("aria-label", "Save " + name);
      save.addEventListener("click", function () {
        save.disabled = true;
        call("PATCH", "/api/timetable/classes/" + cls.id, li._collect()).then(function (res) {
          save.disabled = false;
          if (!res.ok || res.data.status !== "ok") return say(res.data.message || "Couldn't save that class.", true);
          state.data = res.data;
          renderList();
          say("Saved " + (res.data.classes ? name : "") + ".");
        }).catch(function () { save.disabled = false; say("Network error — not saved.", true); });
      });
      var del = node("button", { type: "button", className: "tt-btn" }, "Remove");
      del.setAttribute("aria-label", "Remove " + name);
      del.addEventListener("click", function () {
        del.disabled = true;
        call("DELETE", "/api/timetable/classes/" + cls.id).then(function (res) {
          if (!res.ok || res.data.status !== "ok") { del.disabled = false; return say(res.data.message || "Couldn't remove it.", true); }
          state.data = res.data;
          renderList();
          say("Removed " + name + ".");
          el.importBtn.focus();
        }).catch(function () { del.disabled = false; say("Network error.", true); });
      });
      foot.appendChild(save);
      foot.appendChild(del);
    } else {
      var drop = node("button", { type: "button", className: "tt-btn" }, "Leave out");
      drop.setAttribute("aria-label", "Leave out " + name);
      drop.addEventListener("click", function () { li.remove(); });
      foot.appendChild(drop);
    }
    li.appendChild(foot);
    return li;
  }

  function renderList() {
    var data = state.data;
    el.list.innerHTML = "";
    if (!data.classes.length) {
      el.list.appendChild(node("li", { className: "tt-empty" }, "No classes yet. Import them, scan a photo, or add one."));
    }
    data.classes.forEach(function (c) { el.list.appendChild(classEditor(c, false)); });
    el.today.textContent = data.today.rotation_label ? "Today: " + data.today.rotation_label : "";
    renderSettings();
    renderAddRotation();
  }

  function renderSettings() {
    var s = state.data.settings;
    el.kind.value = s.kind;
    if (s.kind === "cycle") el.length.value = s.length;
    el.lengthWrap.hidden = s.kind !== "cycle";
    fillTodayIs(s);
    var days = s.school_weekdays || [];
    Array.prototype.forEach.call(el.schoolDays.querySelectorAll("input"), function (i) {
      i.checked = days.indexOf(i.value) !== -1;
    });
    el.skips.innerHTML = "";
    (s.skip_days || []).forEach(function (skip, idx) {
      var li = node("li");
      var when = skip.start === skip.end ? skip.start : skip.start + " to " + skip.end;
      li.appendChild(node("span", null, when + (skip.label ? " — " + skip.label : "")));
      var rm = node("button", { type: "button", className: "tt-btn" }, "Remove");
      rm.setAttribute("aria-label", "Remove no-school day " + when);
      rm.addEventListener("click", function () {
        var next = s.skip_days.filter(function (_, j) { return j !== idx; });
        saveSettings({ skip_days: next }, "No-school day removed.");
      });
      li.appendChild(rm);
      el.skips.appendChild(li);
    });
    if (!(s.skip_days || []).length) el.skips.appendChild(node("li", { className: "tt-empty" }, "None yet. Rotations skip these days."));
  }

  function fillTodayIs(settings) {
    var labels = rotationLabels(settings);
    el.todayWrap.hidden = !labels.length;
    el.todayIs.innerHTML = "";
    el.todayIs.appendChild(node("option", { value: "" }, "Keep counting"));
    labels.forEach(function (l, i) { el.todayIs.appendChild(node("option", { value: String(i + 1) }, l)); });
    var current = state.data && state.data.today && state.data.today.rotation_day;
    if (current && settings === state.data.settings) el.todayIs.value = String(current);
  }

  function renderAddRotation() {
    el.addRot.innerHTML = "";
    var rot = rotationLabels();
    if (rot.length) {
      el.addRot.appendChild(chips("Rotation days (none = all)", rot.map(function (_, i) { return i + 1; }), rot, [], "ttNewRotDay"));
    }
  }

  function draftSettings() {
    return { kind: el.kind.value, length: Number(el.length.value) || 2 };
  }

  function saveSettings(body, okMessage) {
    body.date = localDate();
    return call("PUT", "/api/timetable/settings", body).then(function (res) {
      if (!res.ok || res.data.status !== "ok") return say(res.data.message || "Couldn't save that.", true);
      state.data = res.data;
      renderList();
      say(okMessage || "Saved.");
    }).catch(function () { say("Network error — not saved.", true); });
  }

  // ── wiring ─────────────────────────────────────────────────────────

  el.kind.addEventListener("change", function () {
    el.lengthWrap.hidden = el.kind.value !== "cycle";
    fillTodayIs(draftSettings());
  });
  el.length.addEventListener("change", function () { fillTodayIs(draftSettings()); });

  el.rotationSave.addEventListener("click", function () {
    var body = {
      kind: el.kind.value,
      school_weekdays: checkedValues(el.schoolDays, "ttSchoolDay")
    };
    if (el.kind.value === "cycle") body.length = Number(el.length.value) || 6;
    if (el.kind.value !== "none" && el.todayIs.value) body.today_is = Number(el.todayIs.value);
    saveSettings(body, "Rotation saved.");
  });

  el.skipAdd.addEventListener("click", function () {
    if (!el.skipStart.value) { say("Pick the first no-school day.", true); el.skipStart.focus(); return; }
    var next = (state.data.settings.skip_days || []).slice();
    next.push({ start: el.skipStart.value, end: el.skipEnd.value || el.skipStart.value, label: el.skipLabel.value.trim() });
    saveSettings({ skip_days: next }, "No-school day added.").then(function () {
      el.skipStart.value = el.skipEnd.value = el.skipLabel.value = "";
    });
  });

  el.addToggle.addEventListener("click", function () {
    var open = el.addForm.hidden;
    el.addForm.hidden = !open;
    el.addToggle.setAttribute("aria-expanded", String(open));
    if (open) $("ttNewCourse").focus();
  });

  el.addForm.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var body = {
      course: $("ttNewCourse").value.trim(),
      period: $("ttNewPeriod").value.trim(),
      room: $("ttNewRoom").value.trim(),
      teacher: $("ttNewTeacher").value.trim(),
      start: $("ttNewStart").value,
      end: $("ttNewEnd").value,
      weekdays: checkedValues(el.addForm, "ttNewDay"),
      rotation_days: checkedValues(el.addForm, "ttNewRotDay").map(Number)
    };
    call("POST", "/api/timetable/classes", body).then(function (res) {
      if (!res.ok || res.data.status !== "ok") return say(res.data.message || "Couldn't add that class.", true);
      state.data = res.data;
      el.addForm.reset();
      renderList();
      say("Added " + body.course + ".");
      $("ttNewCourse").focus();
    }).catch(function () { say("Network error — not added.", true); });
  });

  el.importBtn.addEventListener("click", function () {
    el.importBtn.disabled = true;
    say("Asking your school for your schedule…");
    call("POST", "/api/timetable/import", { source: "auto" }).then(function (res) {
      el.importBtn.disabled = false;
      var d = res.data || {};
      if (!res.ok || d.status !== "ok") return say(d.message || "Import didn't work.", true);
      state.data = d;
      renderList();
      if (!d.imported) return say(d.message || "Your school didn't share a schedule.");
      var bits = ["Imported " + d.imported + " class" + (d.imported === 1 ? "" : "es") + " from " + (d.source === "studentvue" ? "StudentVUE" : "Schoology") + "."];
      if (d.untimed) bits.push(d.untimed + " still need times.");
      if (d.needs_days) bits.push(d.needs_days + " need their rotation days.");
      if (d.today && d.today.needs_anchor) bits.push("Set which day today is below.");
      say(bits.join(" "));
    }).catch(function () { el.importBtn.disabled = false; say("Network error.", true); });
  });

  el.photo.addEventListener("change", function () {
    var file = el.photo.files && el.photo.files[0];
    if (!file) return;
    var form = new FormData();
    form.append("image", file);
    say("Reading your schedule photo…");
    call("POST", "/api/timetable/photo", form).then(function (res) {
      el.photo.value = "";
      var d = res.data || {};
      if (!res.ok || d.status !== "ok") return say(d.message || "Couldn't read that photo. Add your classes by hand.", true);
      state.drafts = d.classes || [];
      el.drafts.innerHTML = "";
      state.drafts.forEach(function (c) { el.drafts.appendChild(classEditor(c, true)); });
      el.draftsWrap.hidden = false;
      var hint = d.rotation_hint;
      if (hint && hint.kind && state.data.settings.kind === "none") {
        el.kind.value = hint.kind;
        if (hint.kind === "cycle") el.length.value = hint.length;
        el.lengthWrap.hidden = hint.kind !== "cycle";
        fillTodayIs(draftSettings());
      }
      say("Found " + state.drafts.length + " class" + (state.drafts.length === 1 ? "" : "es") + ". Check them, then save.");
      var first = el.drafts.querySelector("input");
      if (first) first.focus();
    }).catch(function () { el.photo.value = ""; say("Network error.", true); });
  });

  el.draftsSave.addEventListener("click", function () {
    var rows = Array.prototype.map.call(el.drafts.children, function (li) { return li._collect(); })
      .filter(function (r) { return r.course; });
    if (!rows.length) return say("There are no classes left to save.", true);
    el.draftsSave.disabled = true;
    call("POST", "/api/timetable/classes/bulk", { source: "photo", classes: rows }).then(function (res) {
      el.draftsSave.disabled = false;
      if (!res.ok || res.data.status !== "ok") return say(res.data.message || "Couldn't save those classes.", true);
      state.data = res.data;
      el.drafts.innerHTML = "";
      el.draftsWrap.hidden = true;
      renderList();
      say("Saved " + res.data.saved + " classes.");
    }).catch(function () { el.draftsSave.disabled = false; say("Network error.", true); });
  });

  el.draftsDiscard.addEventListener("click", function () {
    el.drafts.innerHTML = "";
    el.draftsWrap.hidden = true;
    say("Photo results discarded.");
  });

  call("GET", "/api/timetable?date=" + encodeURIComponent(localDate())).then(function (res) {
    if (!res.ok || res.data.status !== "ok") { panel.hidden = true; return; }
    state.data = res.data;
    panel.hidden = false;
    renderList();
    if (res.data.today.needs_anchor) say("Tell us which day today is (under Schedule rotation) so the rotation lines up.");
    if (location.hash === "#timetable") panel.scrollIntoView();
  }).catch(function () { panel.hidden = true; });
})();
