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

    items = q.limit(200).all()
    voted_ids = {
        v.request_id
        for v in FeatureRequestVote.query.filter_by(user_id=current_user.id).all()
    }
    return jsonify({
        "status": "ok",
        "items": [_serialize_request(r, r.id in voted_ids) for r in items],
    })


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
    db.session.commit()
    return jsonify({
        "status": "ok",
        "reminders_enabled": bool(prefs.reminders_enabled),
        "reminder_minutes": int(prefs.reminder_minutes or 45),
        "daily_goal_minutes": int(prefs.daily_goal_minutes or 60),
    })
