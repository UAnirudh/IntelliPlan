"""Owner-controlled school domains, plans, and safe membership resolution."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime

import requests
from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import func

from time_utils import utcnow

bp = Blueprint("school_entitlements", __name__)

SCHOOL_PLAN_TIERS = ("free", "pro", "premium", "premium_byok")
_PLAN_RANK = {"free": 0, "pro": 1, "premium": 2, "premium_byok": 2}
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TXT_PREFIX = "intelliplan-verification="


def normalize_domain(value: object) -> str:
    raw = str(value or "").strip().rstrip(".").lower()
    if not raw or len(raw) > 253 or any(ch in raw for ch in "@/:\\ \t\r\n"):
        raise ValueError("Enter a domain such as district.edu, without a URL or email address.")
    try:
        domain = raw.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        raise ValueError("That domain name is not valid.") from None
    labels = domain.split(".")
    if len(labels) < 2 or any(not _DOMAIN_LABEL.fullmatch(label) for label in labels):
        raise ValueError("Enter a fully qualified domain such as district.edu.")
    return domain


def _models():
    from App import learning_models
    return learning_models


def _require_owner():
    from App import is_owner
    if not is_owner(current_user):
        abort(404)


def _json_body() -> dict:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        abort(400)
    return value


def _audit(organization_id: int, action: str, changes: dict) -> None:
    from App import db
    M = _models()
    try:
        actor_id = current_user.id if current_user.is_authenticated else None
    except (RuntimeError, AttributeError):
        actor_id = None
    db.session.add(M["Audit"](organization_id=organization_id,
                              actor_id=actor_id,
                              action=action, changes=changes))


def _membership_organization_ids(user) -> list[int]:
    if not getattr(user, "is_authenticated", False):
        return []
    M = _models()
    direct = M["Membership"].query.filter_by(user_id=user.id).all()
    ids: list[int] = []
    seen: set[int] = set()
    for membership in direct:
        organization = M["Organization"].query.get(membership.organization_id)
        depth = 0
        while organization and organization.id not in seen and depth < 32:
            seen.add(organization.id)
            ids.append(organization.id)
            organization = (M["Organization"].query.get(organization.parent_id)
                            if organization.parent_id else None)
            depth += 1
    return ids


def school_plan_for(user) -> str | None:
    """Return the strongest owner-assigned plan for the user's institutions."""
    ids = _membership_organization_ids(user)
    if not ids:
        return None
    M = _models()
    rows = M["SchoolPlan"].query.filter(M["SchoolPlan"].organization_id.in_(ids)).all()
    valid = [row.plan_tier for row in rows if row.plan_tier in SCHOOL_PLAN_TIERS]
    return max(valid, key=lambda tier: _PLAN_RANK[tier]) if valid else None


def is_partnered_user(user) -> bool:
    """A verified-domain member or explicitly-plan-assigned school member."""
    ids = _membership_organization_ids(user)
    if not ids:
        return False
    M = _models()
    if M["SchoolPlan"].query.filter(M["SchoolPlan"].organization_id.in_(ids)).first():
        return True
    return M["SchoolDomain"].query.filter(
        M["SchoolDomain"].organization_id.in_(ids),
        M["SchoolDomain"].verified_at.isnot(None),
    ).first() is not None


def add_verified_school_membership(user, email: str | None = None) -> bool:
    """Assign a member only when their stored email and verification flag agree.

    Called after Google userinfo explicitly confirms ``email_verified=true``.
    A self-entered password-account address is never sufficient.
    """
    normalized = str(email or getattr(user, "email", "") or "").strip().lower()
    if (not getattr(user, "is_authenticated", True) or not getattr(user, "id", None)
            or getattr(user, "email_verified", False) is not True
            or normalized != str(getattr(user, "email", "") or "").strip().lower()
            or "@" not in normalized):
        return False
    domain = normalized.rsplit("@", 1)[1]
    M = _models()
    verified = M["SchoolDomain"].query.filter_by(domain=domain).filter(
        M["SchoolDomain"].verified_at.isnot(None)).first()
    if verified is None:
        return False
    membership = M["Membership"].query.filter_by(
        user_id=user.id, organization_id=verified.organization_id).first()
    if membership:
        return False
    role = "teacher" if getattr(user, "role", "student") == "teacher" else "student"
    from App import db
    db.session.add(M["Membership"](user_id=user.id, organization_id=verified.organization_id, role=role))
    _audit(verified.organization_id, "verified_domain_member_added", {
        "user_id": int(user.id), "role": role, "domain_id": int(verified.id),
    })
    return True


def _record_for_org(organization_id: int):
    M = _models()
    organization = M["Organization"].query.get(organization_id)
    if not organization or organization.kind not in {"school", "district"}:
        abort(404)
    return organization


def _dns_txt_contains(domain: str, expected_hash: str) -> bool:
    """Verify the owner's one-time TXT proof through Google's DNS-over-HTTPS."""
    try:
        response = requests.get(
            "https://dns.google/resolve",
            params={"name": f"_intelliplan.{domain}", "type": "TXT"},
            headers={"Accept": "application/dns-json"}, timeout=6,
        )
        if response.status_code != 200:
            return False
        answers = response.json().get("Answer") or []
    except Exception:
        return False
    for answer in answers:
        if not isinstance(answer, dict) or answer.get("type") != 16:
            continue
        # TXT RDATA can be one quoted string or several concatenated chunks.
        raw = str(answer.get("data") or "").replace('" "', "").strip('"')
        if raw.startswith(_TXT_PREFIX):
            supplied = raw[len(_TXT_PREFIX):]
            if hmac.compare_digest(hashlib.sha256(supplied.encode()).hexdigest(), expected_hash):
                return True
    return False


@bp.route("/owner/schools")
def owner_schools_page():
    _require_owner()
    return render_template("owner_schools.html", active_page="admin")


@bp.route("/api/owner/schools", methods=["GET", "POST"])
def owner_schools():
    _require_owner()
    from App import db
    M = _models()
    if request.method == "POST":
        data = _json_body()
        kind = data.get("kind")
        if kind not in {"school", "district"}:
            return jsonify(error="invalid_kind", message="Choose a school or district."), 400
        parent_id = data.get("parent_id")
        if parent_id is not None:
            if type(parent_id) is not int:
                return jsonify(error="invalid_parent"), 400
            parent = M["Organization"].query.get(parent_id)
            if not parent or (kind == "school" and parent.kind != "district") or kind == "district":
                return jsonify(error="invalid_parent", message="Choose a district as the parent of this school."), 400
        name = str(data.get("name") or "").strip()[:160]
        if len(name) < 2:
            return jsonify(error="invalid_name", message="Enter a school or district name."), 400
        organization = M["Organization"](name=name, kind=kind, parent_id=parent_id, policy={})
        db.session.add(organization)
        db.session.flush()
        _audit(organization.id, "owner_school_created", {"name": name, "kind": kind})
        db.session.commit()
        return jsonify(id=organization.id), 201

    organizations = M["Organization"].query.filter(
        M["Organization"].kind.in_(["school", "district"])
    ).order_by(M["Organization"].name.asc()).all()
    ids = [organization.id for organization in organizations]
    plan_rows = M["SchoolPlan"].query.filter(M["SchoolPlan"].organization_id.in_(ids)).all() if ids else []
    plans_by_org = {row.organization_id: row.plan_tier for row in plan_rows}
    domains = M["SchoolDomain"].query.filter(M["SchoolDomain"].organization_id.in_(ids)).all() if ids else []
    domains_by_org: dict[int, list[dict]] = {oid: [] for oid in ids}
    for row in domains:
        domains_by_org.setdefault(row.organization_id, []).append({
            "id": row.id, "domain": row.domain, "verified": bool(row.verified_at),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        })
    counts: dict[int, dict[str, int]] = {oid: {"members": 0, "students": 0, "teachers": 0, "admins": 0}
                                        for oid in ids}
    if ids:
        grouped = db.session.query(M["Membership"].organization_id, M["Membership"].role,
                                   func.count(M["Membership"].id)).filter(
            M["Membership"].organization_id.in_(ids)).group_by(
                M["Membership"].organization_id, M["Membership"].role).all()
        for oid, role, count in grouped:
            if oid in counts:
                counts[oid]["members"] += int(count)
                key = role + "s" if role in {"student", "teacher", "admin"} else "members"
                if key in counts[oid]:
                    counts[oid][key] += int(count)
    return jsonify(schools=[{
        "id": organization.id, "name": organization.name, "kind": organization.kind,
        "parent_id": organization.parent_id, "plan_tier": plans_by_org.get(organization.id, "free"),
        "plan_configured": organization.id in plans_by_org,
        "members": counts[organization.id], "domains": domains_by_org.get(organization.id, []),
    } for organization in organizations], plan_tiers=list(SCHOOL_PLAN_TIERS))


@bp.route("/api/owner/schools/<int:organization_id>/plan", methods=["PUT"])
def owner_school_plan(organization_id: int):
    _require_owner()
    from App import db
    organization = _record_for_org(organization_id)
    data = _json_body()
    tier = data.get("plan_tier")
    if tier not in SCHOOL_PLAN_TIERS:
        return jsonify(error="invalid_plan", message="Choose a supported school plan."), 400
    M = _models()
    row = M["SchoolPlan"].query.filter_by(organization_id=organization_id).first()
    previous = row.plan_tier if row else "free"
    if row is None:
        row = M["SchoolPlan"](organization_id=organization_id, plan_tier=tier,
                               updated_by_id=current_user.id)
        db.session.add(row)
    else:
        row.plan_tier = tier
        row.updated_by_id = current_user.id
        row.updated_at = utcnow()
    _audit(organization.id, "school_plan_updated", {"from": previous, "to": tier})
    db.session.commit()
    return jsonify(status="ok", plan_tier=tier)


@bp.route("/api/owner/schools/<int:organization_id>/domains", methods=["POST"])
def owner_school_domain_add(organization_id: int):
    _require_owner()
    from App import db
    organization = _record_for_org(organization_id)
    M = _models()
    try:
        domain = normalize_domain(_json_body().get("domain"))
    except ValueError as exc:
        return jsonify(error="invalid_domain", message=str(exc)), 400
    if M["SchoolDomain"].query.filter_by(domain=domain).first():
        return jsonify(error="domain_exists", message="That domain is already registered."), 409
    token = secrets.token_urlsafe(32)
    row = M["SchoolDomain"](organization_id=organization.id, domain=domain,
                            verification_hash=hashlib.sha256(token.encode()).hexdigest(),
                            created_by_id=current_user.id)
    db.session.add(row)
    _audit(organization.id, "school_domain_added", {"domain": domain, "verified": False})
    db.session.commit()
    return jsonify(domain_id=row.id, domain=domain, verified=False,
                   txt_name=f"_intelliplan.{domain}", txt_value=f"{_TXT_PREFIX}{token}"), 201


@bp.route("/api/owner/schools/<int:organization_id>/domains/<int:domain_id>/verify", methods=["POST"])
def owner_school_domain_verify(organization_id: int, domain_id: int):
    _require_owner()
    from App import db
    organization = _record_for_org(organization_id)
    M = _models()
    row = M["SchoolDomain"].query.filter_by(id=domain_id, organization_id=organization.id).first_or_404()
    if row.verified_at:
        return jsonify(status="ok", verified=True)
    if not _dns_txt_contains(row.domain, row.verification_hash):
        return jsonify(error="proof_not_found", message="The verification TXT record was not found. DNS updates can take a few minutes; check the name and value and retry."), 409
    row.verified_at = utcnow()
    row.verified_by_id = current_user.id
    _audit(organization.id, "school_domain_verified", {"domain": row.domain})
    db.session.commit()
    return jsonify(status="ok", verified=True, verified_at=row.verified_at.isoformat())


@bp.route("/api/owner/schools/<int:organization_id>/domains/<int:domain_id>", methods=["DELETE"])
def owner_school_domain_delete(organization_id: int, domain_id: int):
    _require_owner()
    from App import db
    organization = _record_for_org(organization_id)
    M = _models()
    row = M["SchoolDomain"].query.filter_by(id=domain_id, organization_id=organization.id).first_or_404()
    domain = row.domain
    db.session.delete(row)
    _audit(organization.id, "school_domain_removed", {"domain": domain})
    db.session.commit()
    return jsonify(status="ok")


@bp.route("/api/owner/schools/<int:organization_id>/audit")
def owner_school_audit(organization_id: int):
    _require_owner()
    _record_for_org(organization_id)
    M = _models()
    rows = M["Audit"].query.filter_by(organization_id=organization_id).order_by(
        M["Audit"].id.desc()).limit(100).all()
    return jsonify(events=[{
        "id": row.id, "action": row.action, "actor_id": row.actor_id,
        "changes": row.changes, "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows])


def install(app):
    app.register_blueprint(bp)
