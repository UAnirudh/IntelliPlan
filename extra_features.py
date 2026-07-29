"""Feature Requests, Text Dissector, and Digital Media Balance.

Three small additions surfaced as a single blueprint to keep App.py from
growing further. Models live in App.py (FeatureRequest,
FeatureRequestVote, MediaBalanceSession, MediaBalancePrefs); this module
owns routes and AI calls.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from ai_provider import ai_available, chat as ai_chat

logger = logging.getLogger(__name__)

extras_bp = Blueprint("extras", __name__)


# ── Feature Requests ───────────────────────────────────────────────

ALLOWED_CATEGORIES = {"general", "tutor", "scheduler", "grades", "ui", "integrations", "bug"}
ALLOWED_STATUSES = {"open", "planned", "in_progress", "shipped", "declined"}


def _serialize_request(req, voted: bool) -> dict:
    return {
        "id": req.id,
        "title": req.title,
        "body": req.body or "",
        "category": req.category or "general",
        "status": req.status or "open",
        "vote_count": int(req.vote_count or 0),
        "voted": voted,
        "created_at": req.created_at.isoformat() if req.created_at else None,
    }


@extras_bp.route("/features")
@login_required
def features_page():
    return render_template("features.html", active_page="features")


@extras_bp.route("/api/features", methods=["GET"])
@login_required
def api_features_list():
    from App import FeatureRequest, FeatureRequestVote

    sort = request.args.get("sort", "top")
    status = request.args.get("status", "all")
    q = FeatureRequest.query
    if status in ALLOWED_STATUSES:
        q = q.filter_by(status=status)
    elif status != "all":
        q = q.filter_by(status="open")

    if sort == "new":
        q = q.order_by(FeatureRequest.created_at.desc())
    else:
        q = q.order_by(FeatureRequest.vote_count.desc(), FeatureRequest.created_at.desc())

    # Paged rather than a flat top-200 cap, which silently hid every request
    # past the 200th once the board filled up.
    from App import paginate_query

    payload = paginate_query(q, lambda r: r, default_size=30)
    rows = payload["items"]

    # Only look up votes for the ids on this page.
    voted_ids = set()
    if rows:
        voted_ids = {
            v.request_id
            for v in FeatureRequestVote.query.filter(
                FeatureRequestVote.user_id == current_user.id,
                FeatureRequestVote.request_id.in_([r.id for r in rows]),
            ).all()
        }
    payload["items"] = [_serialize_request(r, r.id in voted_ids) for r in rows]
    return jsonify(payload)


@extras_bp.route("/api/features", methods=["POST"])
@login_required
def api_features_create():
    from App import FeatureRequest, db

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    category = (data.get("category") or "general").strip().lower()
    if not title or len(title) < 4:
        return jsonify({"status": "error", "message": "Title must be at least 4 characters."}), 400
    if len(title) > 140:
        return jsonify({"status": "error", "message": "Title too long (max 140 chars)."}), 400
    if len(body) > 4000:
        return jsonify({"status": "error", "message": "Description too long (max 4000 chars)."}), 400
    if category not in ALLOWED_CATEGORIES:
        category = "general"

    cutoff = datetime.utcnow() - timedelta(minutes=2)
    recent = FeatureRequest.query.filter(
        FeatureRequest.user_id == current_user.id,
        FeatureRequest.created_at > cutoff,
    ).count()
    if recent >= 3:
        return jsonify({"status": "error", "message": "Please wait a moment before submitting again."}), 429

    req = FeatureRequest(
        user_id=current_user.id,
        title=title,
        body=body,
        category=category,
        status="open",
        vote_count=0,
    )
    db.session.add(req)
    db.session.commit()
    return jsonify({"status": "ok", "item": _serialize_request(req, voted=False)})


@extras_bp.route("/api/features/<int:req_id>/vote", methods=["POST"])
@login_required
def api_features_vote(req_id: int):
    from App import FeatureRequest, FeatureRequestVote, db

    req = FeatureRequest.query.get(req_id)
    if not req:
        return jsonify({"status": "error", "message": "Not found."}), 404
    existing = FeatureRequestVote.query.filter_by(
        request_id=req_id, user_id=current_user.id
    ).first()
    if existing:
        db.session.delete(existing)
        req.vote_count = max(0, (req.vote_count or 0) - 1)
        voted = False
    else:
        db.session.add(FeatureRequestVote(request_id=req_id, user_id=current_user.id))
        req.vote_count = (req.vote_count or 0) + 1
        voted = True
    db.session.commit()
    return jsonify({"status": "ok", "voted": voted, "vote_count": int(req.vote_count or 0)})


# ── Text Dissector ─────────────────────────────────────────────────

DISSECTOR_SYSTEM = (
    "You are a study-focused reading assistant for students. Given a passage of text, "
    "produce a structured analysis as strict JSON with these keys: "
    "summary (1-3 sentences, plain language), "
    "key_points (array of 3-8 short bullet strings), "
    "vocabulary (array of {term, meaning} for terms a high-schooler might not know — empty array if none), "
    "questions (array of 3 short comprehension questions). "
    "Output ONLY valid JSON. No commentary, no markdown fences."
)


def _dissector_fallback(text: str) -> dict:
    """Deterministic fallback used when no AI provider is configured.
    Keeps the page functional in dev/CI without surprising users."""
    snippet = text.strip().split()
    head = " ".join(snippet[:40])
    bullets = []
    for sentence in text.replace("\n", " ").split("."):
        s = sentence.strip()
        if 20 < len(s) < 200 and len(bullets) < 5:
            bullets.append(s)
    return {
        "summary": (head + ("…" if len(snippet) > 40 else "")) or "No text provided.",
        "key_points": bullets or ["Add more text to extract key points."],
        "vocabulary": [],
        "questions": [
            "What is the main idea?",
            "Which sentence best supports the main idea?",
            "How would you explain this to a classmate?",
        ],
        "_fallback": True,
    }


@extras_bp.route("/tools/text-dissector")
def text_dissector_page():
    return render_template("text_dissector.html", active_page="text_dissector")


@extras_bp.route("/api/text-dissector", methods=["POST"])
def api_text_dissector():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"status": "error", "message": "Paste some text first."}), 400
    if len(text) > 12000:
        return jsonify({"status": "error", "message": "Text too long (max 12,000 characters)."}), 400

    if not ai_available():
        return jsonify({"status": "ok", "analysis": _dissector_fallback(text)})

    try:
        raw = ai_chat(
            [
                {"role": "system", "content": DISSECTOR_SYSTEM},
                {"role": "user", "content": text},
            ],
            tier="fast",
            temperature=0.2,
            max_tokens=900,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning("dissector AI failed: %s", exc)
        return jsonify({"status": "ok", "analysis": _dissector_fallback(text)})

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        analysis = json.loads(cleaned)
    except Exception:
        analysis = _dissector_fallback(text)
        analysis["_parse_error"] = True

    analysis.setdefault("summary", "")
    analysis.setdefault("key_points", [])
    analysis.setdefault("vocabulary", [])
    analysis.setdefault("questions", [])
    return jsonify({"status": "ok", "analysis": analysis})


# ── Digital Media Balance ──────────────────────────────────────────

def _today_local(offset_minutes: int) -> str:
    """Return YYYY-MM-DD for the user's local date. Client sends its
    UTC offset so each user's day boundary is its own."""
    now = datetime.utcnow() - timedelta(minutes=offset_minutes)
    return now.strftime("%Y-%m-%d")


def _local_hour(offset_minutes: int) -> int:
    """Return the user's current local hour (0-23)."""
    now = datetime.utcnow() - timedelta(minutes=offset_minutes)
    return now.hour


# Daytime nudges: short, energizing, never pushy. Rotation prevents repetition.
DAY_MESSAGES = [
    "You've been working for a while. Stand up, stretch, drink some water.",
    "Quick eye break — look at something 20 feet away for 20 seconds. Your focus will thank you.",
    "Long session. A 2-minute walk now can make the next hour sharper.",
    "Refill the water bottle. Hydration > willpower.",
    "Shake your hands out. Roll your shoulders. You're doing great.",
]

# Evening nudges: bridge between focused work and winding down.
EVENING_MESSAGES = [
    "Sun's down. Try to wrap the hardest task first so the rest is downhill.",
    "Long study night ahead? Save the easy stuff for last — fatigue hits everyone.",
    "Good time for a real meal. Brains run on glucose, not vibes.",
]

# Night nudges: progressively stronger pull toward sleep.
NIGHT_MESSAGES = [
    "Sleep is when memory consolidates. What you studied today sticks better after rest.",
    "It's late. Consider wrapping up — diminishing returns kick in fast at this hour.",
    "Your brain is asking for sleep. Tomorrow-you will be much sharper.",
    "Late-night cramming has the worst return on effort. Try a fresh start in the morning.",
    "Long-term recall doubles when you sleep 7+ hours after studying. Worth it.",
]


def _nudge_for_hour(hour: int) -> dict:
    """Pick a wellness message bucket based on the user's local hour.

    Day window: 9:00–18:59. Evening: 19:00–21:59. Night: 22:00–08:59.
    The exact bucket boundaries are intentionally fuzzy — students study
    on weird schedules and we don't want to be wrong loudly.
    """
    if 9 <= hour <= 18:
        bucket = "day"
        pool = DAY_MESSAGES
    elif 19 <= hour <= 21:
        bucket = "evening"
        pool = EVENING_MESSAGES
    else:
        bucket = "night"
        pool = NIGHT_MESSAGES
    # Rotate by current minute so consecutive pings don't repeat the same line.
    idx = datetime.utcnow().minute % len(pool)
    return {"bucket": bucket, "message": pool[idx]}


def _suggestions(today_minutes: int, week_minutes: int, goal: int) -> list[str]:
    """Awareness-only suggestions. Never restrictive."""
    tips: list[str] = []
    if today_minutes >= goal and goal > 0:
        tips.append(f"You've hit your awareness goal of {goal} min today — great pace.")
    if today_minutes >= 120:
        tips.append("Long stretch today. A 5-minute stretch break can reset your focus.")
    if week_minutes >= 600:
        tips.append("Heavy week. Consider one screen-free hour before bed for better sleep.")
    if today_minutes < 15 and week_minutes < 60:
        tips.append("Light usage. If you have a busy week ahead, a quick scheduler check helps.")
    if not tips:
        tips.append("Balanced usage. Keep going — small consistent sessions beat marathon ones.")
    return tips


@extras_bp.route("/balance")
@login_required
def balance_page():
    return render_template("balance.html", active_page="balance")


@extras_bp.route("/api/balance/ping", methods=["POST"])
@login_required
def api_balance_ping():
    from App import MediaBalanceSession, db

    data = request.get_json(silent=True) or {}
    try:
        minutes = max(0, min(int(data.get("minutes", 1)), 15))
    except (TypeError, ValueError):
        minutes = 1
    try:
        tz_offset = int(data.get("tz_offset", 0))
    except (TypeError, ValueError):
        tz_offset = 0
    local = _today_local(tz_offset)

    row = MediaBalanceSession.query.filter_by(
        user_id=current_user.id, local_date=local
    ).first()
    if row is None:
        row = MediaBalanceSession(user_id=current_user.id, local_date=local, minutes=minutes)
        db.session.add(row)
    else:
        row.minutes = (row.minutes or 0) + minutes
    db.session.commit()
    return jsonify({"status": "ok", "today_minutes": int(row.minutes or 0)})


@extras_bp.route("/api/balance/insights", methods=["GET"])
@login_required
def api_balance_insights():
    from App import MediaBalanceSession, MediaBalancePrefs

    try:
        tz_offset = int(request.args.get("tz_offset", 0))
    except (TypeError, ValueError):
        tz_offset = 0
    today = _today_local(tz_offset)
    week_dates = [
        (datetime.utcnow() - timedelta(minutes=tz_offset, days=i)).strftime("%Y-%m-%d")
        for i in range(7)
    ]

    rows = MediaBalanceSession.query.filter(
        MediaBalanceSession.user_id == current_user.id,
        MediaBalanceSession.local_date.in_(week_dates),
    ).all()
    by_date = {r.local_date: int(r.minutes or 0) for r in rows}
    today_minutes = by_date.get(today, 0)
    week_minutes = sum(by_date.values())

    prefs = MediaBalancePrefs.query.filter_by(user_id=current_user.id).first()
    goal = int(prefs.daily_goal_minutes) if prefs and prefs.daily_goal_minutes else 60
    reminders_enabled = bool(prefs.reminders_enabled) if prefs else False
    reminder_minutes = int(prefs.reminder_minutes) if prefs and prefs.reminder_minutes else 45

    return jsonify({
        "status": "ok",
        "today_minutes": today_minutes,
        "week_minutes": week_minutes,
        "week_series": [
            {"date": d, "minutes": by_date.get(d, 0)} for d in reversed(week_dates)
        ],
        "daily_goal_minutes": goal,
        "reminders_enabled": reminders_enabled,
        "reminder_minutes": reminder_minutes,
        "suggestions": _suggestions(today_minutes, week_minutes, goal),
    })


@extras_bp.route("/api/balance/prefs", methods=["POST"])
@login_required
def api_balance_prefs():
    from App import MediaBalancePrefs, db

    data = request.get_json(silent=True) or {}
    prefs = MediaBalancePrefs.query.filter_by(user_id=current_user.id).first()
    if prefs is None:
        prefs = MediaBalancePrefs(user_id=current_user.id)
        db.session.add(prefs)

    if "reminders_enabled" in data:
        prefs.reminders_enabled = bool(data["reminders_enabled"])
    if "reminder_minutes" in data:
        try:
            prefs.reminder_minutes = max(10, min(int(data["reminder_minutes"]), 240))
        except (TypeError, ValueError):
            pass
    if "daily_goal_minutes" in data:
        try:
            prefs.daily_goal_minutes = max(0, min(int(data["daily_goal_minutes"]), 600))
        except (TypeError, ValueError):
            pass
    if "night_nudges_enabled" in data:
        prefs.night_nudges_enabled = bool(data["night_nudges_enabled"])
    if "night_start_hour" in data:
        try:
            prefs.night_start_hour = max(18, min(int(data["night_start_hour"]), 26)) % 24
        except (TypeError, ValueError):
            pass
    if "night_cadence_minutes" in data:
        try:
            prefs.night_cadence_minutes = max(5, min(int(data["night_cadence_minutes"]), 60))
        except (TypeError, ValueError):
            pass
    db.session.commit()
    return jsonify({
        "status": "ok",
        "reminders_enabled": bool(prefs.reminders_enabled),
        "reminder_minutes": int(prefs.reminder_minutes or 45),
        "daily_goal_minutes": int(prefs.daily_goal_minutes or 60),
        "night_nudges_enabled": bool(prefs.night_nudges_enabled),
        "night_start_hour": int(prefs.night_start_hour if prefs.night_start_hour is not None else 22),
        "night_cadence_minutes": int(prefs.night_cadence_minutes or 10),
    })


@extras_bp.route("/api/balance/nudge", methods=["GET"])
@login_required
def api_balance_nudge():
    """Return a time-of-day-appropriate wellness message and the
    cadence at which the client should next ask. Used by the global
    in-app nudger to throttle itself without hammering the server.
    """
    from App import MediaBalancePrefs

    try:
        tz_offset = int(request.args.get("tz_offset", 0))
    except (TypeError, ValueError):
        tz_offset = 0
    hour = _local_hour(tz_offset)

    prefs = MediaBalancePrefs.query.filter_by(user_id=current_user.id).first()
    day_enabled = bool(prefs.reminders_enabled) if prefs else False
    night_enabled = bool(prefs.night_nudges_enabled) if prefs is None or prefs.night_nudges_enabled is None else bool(prefs.night_nudges_enabled)
    if prefs is None:
        night_enabled = True
    day_cadence = int(prefs.reminder_minutes) if prefs and prefs.reminder_minutes else 45
    night_cadence = int(prefs.night_cadence_minutes) if prefs and prefs.night_cadence_minutes else 10
    night_start = int(prefs.night_start_hour) if prefs and prefs.night_start_hour is not None else 22

    is_night = hour >= night_start or hour < 6
    nudge = _nudge_for_hour(hour)

    show = False
    cadence = day_cadence
    if is_night and night_enabled:
        show = True
        cadence = night_cadence
    elif not is_night and day_enabled:
        show = True
        cadence = day_cadence

    return jsonify({
        "status": "ok",
        "show": show,
        "bucket": nudge["bucket"],
        "message": nudge["message"],
        "cadence_minutes": cadence,
        "local_hour": hour,
        "is_night": is_night,
    })


# ── Accessibility prefs ────────────────────────────────────────────

ALLOWED_SCALES = {90, 100, 115, 130, 150}
ALLOWED_LINE_SPACING = {100, 130, 160, 200}


def _a11y_payload(prefs) -> dict:
    if prefs is None:
        return {
            "dyslexia_font": False,
            "text_scale": 100,
            "line_spacing": 100,
            "high_contrast": False,
            "reduced_motion": False,
            "underline_links": False,
            "focus_ring_bold": False,
        }
    return {
        "dyslexia_font": bool(prefs.dyslexia_font),
        "text_scale": int(prefs.text_scale or 100),
        "line_spacing": int(prefs.line_spacing or 100),
        "high_contrast": bool(prefs.high_contrast),
        "reduced_motion": bool(prefs.reduced_motion),
        "underline_links": bool(prefs.underline_links),
        "focus_ring_bold": bool(prefs.focus_ring_bold),
    }


@extras_bp.route("/api/a11y/prefs", methods=["GET"])
@login_required
def api_a11y_get():
    from App import AccessibilityPrefs

    prefs = AccessibilityPrefs.query.filter_by(user_id=current_user.id).first()
    return jsonify({"status": "ok", "prefs": _a11y_payload(prefs)})


@extras_bp.route("/api/a11y/prefs", methods=["POST"])
@login_required
def api_a11y_set():
    from App import AccessibilityPrefs, db

    data = request.get_json(silent=True) or {}
    prefs = AccessibilityPrefs.query.filter_by(user_id=current_user.id).first()
    if prefs is None:
        prefs = AccessibilityPrefs(user_id=current_user.id)
        db.session.add(prefs)

    if "dyslexia_font" in data:
        prefs.dyslexia_font = bool(data["dyslexia_font"])
    if "text_scale" in data:
        try:
            v = int(data["text_scale"])
            if v in ALLOWED_SCALES:
                prefs.text_scale = v
        except (TypeError, ValueError):
            pass
    if "line_spacing" in data:
        try:
            v = int(data["line_spacing"])
            if v in ALLOWED_LINE_SPACING:
                prefs.line_spacing = v
        except (TypeError, ValueError):
            pass
    if "high_contrast" in data:
        prefs.high_contrast = bool(data["high_contrast"])
    if "reduced_motion" in data:
        prefs.reduced_motion = bool(data["reduced_motion"])
    if "underline_links" in data:
        prefs.underline_links = bool(data["underline_links"])
    if "focus_ring_bold" in data:
        prefs.focus_ring_bold = bool(data["focus_ring_bold"])
    db.session.commit()
    return jsonify({"status": "ok", "prefs": _a11y_payload(prefs)})


@extras_bp.route("/accessibility")
@login_required
def accessibility_page():
    return render_template("accessibility.html", active_page="accessibility")
