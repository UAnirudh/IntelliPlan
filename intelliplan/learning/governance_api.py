"""Versioned beta enrollment and scoped operational review, reusing tutor scoring."""

from datetime import datetime
import json
import click
from flask import abort, jsonify, request
from flask_login import current_user
from intelliplan.learning import policy, governance


def install(bp, app):
    from intelliplan.learning.api import body, gate

    @bp.route("/api/learning/beta/<feature>", methods=["GET", "POST"])
    def enrollment(feature):
        if not current_user.is_authenticated:
            abort(401)
        definition = policy.FEATURES.get(feature)
        if not definition or definition.maturity != "opt_in_beta":
            abort(404)
        from App import db

        M = policy.models()
        row = (
            M["BetaEnrollment"]
            .query.filter_by(user_id=current_user.id, feature=feature)
            .first()
        )
        if request.method == "POST":
            data = body()
            if data.get("accept") is False:
                if row:
                    row.accepted_at, row.consent_version = None, None
            else:
                from adaptive_tutor.api import _check_access

                age_error = _check_access()
                if age_error:
                    return age_error
                if (
                    data.get("accept") is not True
                    or data.get("notice_version") != policy.BETA_NOTICE_VERSION
                ):
                    raise ValueError("Confirm the current beta notice explicitly.")
                _, reason = policy.decision(current_user, feature)
                if reason not in {"allowed", "beta_consent_required"}:
                    return jsonify(error=reason), 403
                row.consent_version, row.accepted_at = (
                    policy.BETA_NOTICE_VERSION,
                    datetime.utcnow(),
                )
            db.session.commit()
        allowed, reason = policy.decision(current_user, feature)
        return jsonify(
            allowed=allowed,
            reason=reason,
            approved=bool(row and row.approved),
            accepted=bool(
                row
                and row.accepted_at
                and row.consent_version == policy.BETA_NOTICE_VERSION
            ),
            notice_version=policy.BETA_NOTICE_VERSION,
            notice="This is an optional preview using existing reviewed practice. Results are limited practice evidence, not validated mastery. You can withdraw at any time. Feedback is visible to your institution administrators; omit personal details and assessed questions.",
            limitation=definition.limitation,
        )

    @bp.route("/api/school-admin/organizations/<int:oid>/beta", methods=["GET", "POST"])
    def participants(oid):
        if not policy.administers(current_user, oid):
            abort(403)
        from App import db

        M = policy.models()
        if request.method == "POST":
            data = body()
            uid, feature, approved = (
                data.get("user_id"),
                data.get("feature"),
                data.get("approved"),
            )
            if (
                type(uid) is not int
                or type(approved) is not bool
                or feature not in policy.FEATURES
                or policy.FEATURES[feature].maturity != "opt_in_beta"
            ):
                raise ValueError(
                    "Provide a member ID, opt-in feature and approval choice."
                )
            if (
                not M["Membership"]
                .query.filter_by(user_id=uid, organization_id=oid)
                .first()
            ):
                abort(404)
            row = (
                M["BetaEnrollment"]
                .query.filter_by(user_id=uid, feature=feature)
                .first()
            )
            if row and row.organization_id != oid:
                return jsonify(error="approval_scope_conflict"), 409
            if not row:
                row = M["BetaEnrollment"](
                    user_id=uid, feature=feature, organization_id=oid
                )
                db.session.add(row)
            row.approved = approved
            if not approved:
                row.accepted_at, row.consent_version = None, None
            db.session.add(
                M["Audit"](
                    organization_id=oid,
                    actor_id=current_user.id,
                    action="beta_approval",
                    changes=dict(user_id=uid, feature=feature, approved=approved),
                )
            )
            db.session.commit()
        return jsonify(
            participants=[
                dict(
                    user_id=r.user_id,
                    feature=r.feature,
                    approved=r.approved,
                    accepted=bool(
                        r.accepted_at
                        and r.consent_version == policy.BETA_NOTICE_VERSION
                    ),
                )
                for r in M["BetaEnrollment"]
                .query.filter_by(organization_id=oid)
                .limit(200)
            ]
        )

    @bp.route("/api/learning/beta/<feature>/feedback", methods=["POST"])
    def feedback(feature):
        denied = gate(feature)
        if denied:
            return denied
        from App import db
        from intelliplan.learning.courses import text

        M = policy.models()
        row = (
            M["BetaEnrollment"]
            .query.filter_by(user_id=current_user.id, feature=feature, approved=True)
            .first()
        )
        if not row:
            eligible_orgs = policy.organizations(current_user)
            if (
                policy.FEATURES[feature].maturity != "admin_only_beta"
                or not eligible_orgs
            ):
                abort(403)
            organization_id = sorted(eligible_orgs, key=lambda org: org.id)[0].id
        else:
            organization_id = row.organization_id
        from adaptive_tutor.api import _check_access

        age_error = _check_access()
        if age_error:
            return age_error
        data = body()
        if data.get("category") not in {"feedback", "error"}:
            raise ValueError("Choose feedback or error.")
        db.session.add(
            M["Feedback"](
                user_id=current_user.id,
                organization_id=organization_id,
                feature=feature,
                category=data["category"],
                message=text(data.get("message"), 1000),
            )
        )
        db.session.commit()
        return jsonify(saved=True), 201

    @bp.route("/api/school-admin/organizations/<int:oid>/operations")
    def operations(oid):
        if not policy.administers(current_user, oid):
            abort(403)
        M = policy.models()
        return jsonify(
            budgets=[
                dict(month=r.month, reserved_tokens=r.reserved_tokens)
                for r in M["Budget"]
                .query.filter_by(organization_id=oid)
                .order_by(M["Budget"].month.desc())
                .limit(12)
            ],
            feedback=[
                dict(feature=r.feature, category=r.category, message=r.message)
                for r in M["Feedback"]
                .query.filter_by(organization_id=oid)
                .order_by(M["Feedback"].id.desc())
                .limit(50)
            ],
        )

    @bp.route("/api/learning/practice-preview/<action>", methods=["GET", "POST"])
    def practice(action):
        for capability in ("practice_preview", "review"):
            denied = gate(capability)
            if denied:
                return denied
        from adaptive_tutor import api as tutor_api

        if action == "profile":
            age_error = tutor_api._check_access()
            if age_error:
                return age_error
            return (
                tutor_api.get_profile()
                if request.method == "GET"
                else tutor_api.save_profile()
            )

        handlers = {
            "path": ("GET", tutor_api.check_path),
            "check": ("POST", tutor_api.new_check),
            "hint": ("POST", tutor_api.check_hint),
            "answer": ("POST", tutor_api.check_answer),
        }
        if action not in handlers or request.method != handlers[action][0]:
            abort(404)
        return handlers[action][1]()

    @app.cli.command("learning-retention")
    @click.option(
        "--apply",
        is_flag=True,
        help="Delete eligible records after reviewing the dry run.",
    )
    @click.option("--confirm-retention-policy", is_flag=True)
    @click.option("--limit", default=500, type=click.IntRange(1, 5000))
    def retention(apply, confirm_retention_policy, limit):
        if apply and not confirm_retention_policy:
            raise click.ClickException(
                "Applying retention requires --confirm-retention-policy. Review the dry run first."
            )
        candidates = governance.retention_candidates(limit=limit)
        counts = {key: len(value) for key, value in candidates.items()}
        result = dict(mode="applied" if apply else "dry_run", eligible=counts)
        if apply:
            result['deleted'] = governance.apply_retention(candidates)
        click.echo(json.dumps(result))
