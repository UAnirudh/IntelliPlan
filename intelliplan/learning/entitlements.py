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
OWNER_SCHOOL_PAGE_SIZE = 50
OWNER_SCHOOL_PAGE_MAX = 100


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
        for organization_id in _organization_ancestor_ids(membership.organization_id):
            if organization_id not in seen:
                seen.add(organization_id)
                ids.append(organization_id)
    return ids


def _organization_ancestor_ids(organization_id: int) -> list[int]:
    """Return an organization's scope, stopping safely on corrupt hierarchies."""
    M = _models()
    ids: list[int] = []
    seen: set[int] = set()
    organization = M["Organization"].query.get(organization_id)
    while organization and organization.id not in seen and len(ids) < 32:
        seen.add(organization.id)
        ids.append(organization.id)
        organization = (M["Organization"].query.get(organization.parent_id)
                        if organization.parent_id else None)
    return ids


def _verified_school_domain(user):
    """Return the owner-verified domain matching this verified account email."""
    if getattr(user, "email_verified", False) is not True:
        return None
    address = str(getattr(user, "email", "") or "").strip().lower()
    if "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1]
    M = _models()
    return M["SchoolDomain"].query.filter_by(domain=domain).filter(
        M["SchoolDomain"].verified_at.isnot(None)).first()


def school_plan_for(user) -> str | None:
    """Return an owner-assigned plan only inside the verified email's school scope.

    Roster membership remains useful for school features, but an admin-added
    account cannot inherit a paid school plan unless its verified address is
    covered by an owner-verified domain in the same organization hierarchy.
    """
    domain = _verified_school_domain(user)
    if domain is None or not getattr(user, "is_authenticated", False):
        return None
    M = _models()
    domain_scope = set(_organization_ancestor_ids(domain.organization_id))
    memberships = M["Membership"].query.filter_by(user_id=user.id).all()
    eligible_plan_ids: set[int] = set()
    for membership in memberships:
        member_scope = _organization_ancestor_ids(membership.organization_id)
        # A school domain may be registered on a parent district or on a
        # child school. Both must be in the same hierarchy; unrelated school
        # memberships never inherit plans just because the email is partnered
        # elsewhere.
        if domain.organization_id in member_scope or membership.organization_id in domain_scope:
            eligible_plan_ids.update(member_scope)
    if not eligible_plan_ids:
        return None
    rows = M["SchoolPlan"].query.filter(
        M["SchoolPlan"].organization_id.in_(eligible_plan_ids)).all()
    valid = [row.plan_tier for row in rows if row.plan_tier in SCHOOL_PLAN_TIERS]
    return max(valid, key=lambda tier: _PLAN_RANK[tier]) if valid else None


def school_plan_details_for_organization(organization, organizations_by_id, plans_by_org) -> dict:
    """Describe the strongest owner-assigned plan on one org's ancestry.

    The owner UI still edits the direct assignment. Members receive the
    strongest configured tier across their school and district memberships,
    so both owner and school-admin dashboards need the effective tier/source.
    Callers pass preloaded maps to avoid per-row queries.
    """
    direct = plans_by_org.get(organization.id)
    candidates = []
    seen: set[int] = set()
    current = organization
    depth = 0
    while current and current.id not in seen and depth < 32:
        seen.add(current.id)
        row = plans_by_org.get(current.id)
        if row and row.plan_tier in SCHOOL_PLAN_TIERS:
            candidates.append((_PLAN_RANK[row.plan_tier], -depth, row.plan_tier, current))
        current = organizations_by_id.get(current.parent_id) if current.parent_id else None
        depth += 1
    if not candidates:
        return {
            "direct_plan_tier": direct.plan_tier if direct and direct.plan_tier in SCHOOL_PLAN_TIERS else "free",
            "plan_configured": direct is not None,
            "effective_plan_tier": "free",
            "effective_plan_source_id": None,
            "effective_plan_source_name": None,
            "effective_plan_inherited": False,
        }
    _, _, tier, source = max(candidates, key=lambda item: (item[0], item[1]))
    return {
        "direct_plan_tier": direct.plan_tier if direct and direct.plan_tier in SCHOOL_PLAN_TIERS else "free",
        "plan_configured": direct is not None,
        "effective_plan_tier": tier,
        "effective_plan_source_id": source.id,
        "effective_plan_source_name": source.name,
        "effective_plan_inherited": source.id != organization.id,
    }


def is_partnered_user(user) -> bool:
    """Whether verified mailbox proof connects this user to a partner school.

    Domain proof is independent of membership insertion so transient roster
    enrollment failures do not start an individual trial. A plan or manual
    roster entry alone is not proof of school affiliation.
    """
    return _verified_school_domain(user) is not None


def add_verified_school_membership(user, email: str | None = None) -> bool:
    """Assign a member only when their stored email and verification flag agree.

    Called after either Google confirms a verified email or a password account
    consumes its one-time email proof. A self-entered address is never enough.
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


def _enroll_existing_verified_domain_users(domain_row) -> int:
    """Enroll already-verified accounts after the owner proves a school domain.

    Email proof can precede owner DNS proof. Password email links are single
    use, so without this backfill those users would have no way to claim the
    school plan after the owner verifies the domain.
    """
    from App import User, db
    M = _models()
    suffix = "@" + domain_row.domain.lower()
    users = User.query.filter(
        User.email_verified.is_(True),
        func.lower(func.trim(User.email)).endswith(suffix),
    ).yield_per(500)
    existing_user_ids = set(row[0] for row in db.session.query(M["Membership"].user_id).filter_by(
        organization_id=domain_row.organization_id).all())
    enrolled = 0
    for user in users:
        if user.id in existing_user_ids:
            continue
        role = "teacher" if getattr(user, "role", "student") == "teacher" else "student"
        db.session.add(M["Membership"](user_id=user.id,
            organization_id=domain_row.organization_id, role=role))
        _audit(domain_row.organization_id, "verified_domain_member_added", {
            "user_id": int(user.id), "role": role, "domain_id": int(domain_row.id),
        })
        existing_user_ids.add(user.id)
        enrolled += 1
    return enrolled


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

    Organization = M["Organization"]
    base_query = Organization.query.filter(Organization.kind.in_(["school", "district"]))
    total = base_query.count()
    limit = min(OWNER_SCHOOL_PAGE_MAX, max(1, request.args.get(
        "limit", OWNER_SCHOOL_PAGE_SIZE, type=int) or OWNER_SCHOOL_PAGE_SIZE))
    offset = max(0, request.args.get("offset", 0, type=int) or 0)
    organizations = (base_query.order_by(Organization.name.asc(), Organization.id.asc())
                     .offset(offset).limit(limit).all())
    ids = [organization.id for organization in organizations]
    # A school may inherit a plan from its parent district even when that
    # district is on a different page. Fetch only parents referenced here.
    parent_ids = {organization.parent_id for organization in organizations
                  if organization.parent_id and organization.parent_id not in ids}
    parents = Organization.query.filter(Organization.id.in_(parent_ids)).all() if parent_ids else []
    organizations_by_id = {organization.id: organization for organization in [*organizations, *parents]}
    plan_org_ids = set(ids) | parent_ids
    plan_rows = (M["SchoolPlan"].query.filter(
        M["SchoolPlan"].organization_id.in_(plan_org_ids)).all() if plan_org_ids else [])
    plans_by_org = {row.organization_id: row for row in plan_rows}
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
    all_orgs = Organization.kind.in_(["school", "district"])
    total_domains = (db.session.query(func.count(M["SchoolDomain"].id))
                     .join(Organization, Organization.id == M["SchoolDomain"].organization_id)
                     .filter(all_orgs, M["SchoolDomain"].verified_at.isnot(None)).scalar() or 0)
    global_roles = (db.session.query(M["Membership"].role, func.count(M["Membership"].id))
                    .join(Organization, Organization.id == M["Membership"].organization_id)
                    .filter(all_orgs).group_by(M["Membership"].role).all())
    global_counts = {"students": 0, "teachers": 0}
    for role, count in global_roles:
        key = role + "s" if role in {"student", "teacher"} else None
        if key in global_counts:
            global_counts[key] = int(count)
    schools = []
    for organization in organizations:
        plan_details = school_plan_details_for_organization(organization, organizations_by_id, plans_by_org)
        schools.append({
            "id": organization.id, "name": organization.name, "kind": organization.kind,
            "parent_id": organization.parent_id, "plan_tier": plan_details["direct_plan_tier"],
            "plan_configured": plan_details["plan_configured"],
            "effective_plan_tier": plan_details["effective_plan_tier"],
            "effective_plan_source_id": plan_details["effective_plan_source_id"],
            "effective_plan_source_name": plan_details["effective_plan_source_name"],
            "effective_plan_inherited": plan_details["effective_plan_inherited"],
            "members": counts[organization.id], "domains": domains_by_org.get(organization.id, []),
        })
    return jsonify(schools=schools, plan_tiers=list(SCHOOL_PLAN_TIERS),
                   limit=limit, offset=offset, total=total,
                   has_more=offset + len(organizations) < total,
                   summary={"organizations": total, "verified_domains": int(total_domains),
                            **global_counts})


@bp.route("/api/owner/schools/district-options")
def owner_school_district_options():
    """Return a bounded district page for the parent selector."""
    _require_owner()
    Organization = _models()["Organization"]
    query = Organization.query.filter_by(kind="district")
    total = query.count()
    limit = min(OWNER_SCHOOL_PAGE_MAX, max(1, request.args.get(
        "limit", OWNER_SCHOOL_PAGE_SIZE, type=int) or OWNER_SCHOOL_PAGE_SIZE))
    offset = max(0, request.args.get("offset", 0, type=int) or 0)
    rows = (query.order_by(Organization.name.asc(), Organization.id.asc())
            .offset(offset).limit(limit).all())
    return jsonify(districts=[{"id": row.id, "name": row.name} for row in rows],
                   limit=limit, offset=offset, total=total,
                   has_more=offset + len(rows) < total)


@bp.route("/api/owner/schools/<int:organization_id>/members")
def owner_school_members(organization_id: int):
    """Show only school staff in the platform-owner institution console."""
    _require_owner()
    from App import User
    organization = _record_for_org(organization_id)
    M = _models()
    query = (M["Membership"].query
            .join(User, User.id == M["Membership"].user_id)
            .filter(M["Membership"].organization_id == organization.id,
                    M["Membership"].role.in_(("teacher", "admin"))))
    total = query.count()
    limit = min(100, max(1, request.args.get("limit", 50, type=int) or 50))
    offset = max(0, request.args.get("offset", 0, type=int) or 0)
    rows = (query.order_by(M["Membership"].role, User.email, M["Membership"].id)
            .offset(offset).limit(limit).all())
    users = {user.id: user for user in User.query.filter(
        User.id.in_({row.user_id for row in rows})).all()} if rows else {}
    return jsonify(members=[{
        "membership_id": row.id,
        "user_id": row.user_id,
        "role": row.role,
        "email": users[row.user_id].email,
        "name": users[row.user_id].name or "",
    } for row in rows if row.user_id in users], limit=limit, offset=offset,
        total=total, has_more=offset + len(rows) < total)


@bp.route("/api/owner/schools/<int:organization_id>/members/<int:membership_id>", methods=["PUT", "DELETE"])
def owner_school_member(organization_id: int, membership_id: int):
    """Change or revoke an existing staff membership, scoped to this school."""
    _require_owner()
    from App import db
    organization = _record_for_org(organization_id)
    M = _models()
    row = M["Membership"].query.filter_by(
        id=membership_id, organization_id=organization.id
    ).first_or_404()
    if row.role not in {"teacher", "admin"}:
        abort(404)

    if request.method == "DELETE":
        old_role = row.role
        db.session.delete(row)
        _audit(organization.id, "school_staff_access_revoked", {
            "membership_id": int(membership_id), "user_id": int(row.user_id), "role": old_role,
        })
        db.session.commit()
        return jsonify(status="ok", removed=True)

    role = _json_body().get("role")
    if role not in {"teacher", "admin"}:
        return jsonify(error="invalid_role", message="Choose teacher or school admin access."), 400
    previous = row.role
    if previous != role:
        row.role = role
        _audit(organization.id, "school_staff_role_updated", {
            "membership_id": int(membership_id), "user_id": int(row.user_id),
            "from": previous, "to": role,
        })
        db.session.commit()
    return jsonify(status="ok", role=role)


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
    enrolled = _enroll_existing_verified_domain_users(row)
    db.session.commit()
    return jsonify(status="ok", verified=True, verified_at=row.verified_at.isoformat(),
                   enrolled_existing_members=enrolled)


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
