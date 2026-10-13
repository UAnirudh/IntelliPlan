"""Explicit, bounded live text generation smoke test on a disposable database.

Run with --run-live to permit two provider requests. Never uses student data.
Credentials come from the existing local environment/.env through App startup.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-live", action="store_true")
args = parser.parse_args()
if not args.run_live:
    parser.error("--run-live is required; this test makes bounded external requests")
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
with tempfile.TemporaryDirectory(prefix="intelliplan-provider-smoke-") as scratch:
    os.environ.update(
        DATABASE_URL="sqlite:///" + (Path(scratch) / "smoke.db").as_posix(),
        SECRET_KEY="temporary-provider-test-" + os.urandom(24).hex(),
        FLASK_ENV="testing",
        APP_BASE_URL="http://localhost",
        RESEND_API_KEY="",
        SENTRY_DSN="",
        POSTHOG_API_KEY="",
        BILLING_ENABLED="false",
    )
    import App
    import ai_provider
    from intelliplan.learning import courses, policy

    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    result = dict(
        provider="gemini",
        data="synthetic adult administrator and Python learning goal",
        requests_maximum=2,
        video_requests=0,
        status="failed",
    )
    available = ai_provider.gemini_available()
    result.update(
        gemini_contract_flag_enabled=os.getenv(
            "GEMINI_MINORS_CONTRACT_APPROVED", ""
        ).lower()
        in {"1", "true", "yes"},
        vertex_project_configured=bool(os.getenv("GOOGLE_CLOUD_PROJECT")),
        vertex_location_configured=bool(os.getenv("GOOGLE_CLOUD_LOCATION")),
    )
    with App.app.app_context():
        owner = App.User(
            email="provider-smoke@example.test", birth_year=2000, password_hash="x"
        )
        App.db.session.add(owner)
        App.db.session.flush()
        uid = owner.id
        App.ADMIN_EMAILS = App.ADMIN_EMAILS | {owner.email}
        M = policy.models()
        school = M["Organization"](
            name="Disposable provider check",
            kind="school",
            policy={"providers": ["gemini"]},
        )
        App.db.session.add(school)
        App.db.session.flush()
        App.db.session.add(
            M["Membership"](user_id=uid, organization_id=school.id, role="admin")
        )
        App.db.session.add(App.FeatureFlag(key="school_course_generator", enabled=True))
        App.db.session.commit()
        with App.app.test_client() as client:
            with client.session_transaction() as session:
                session["_user_id"] = str(uid)
                session["_fresh"] = True
            begun = time.perf_counter()
            response = client.post(
                "/api/learning/courses",
                json={
                    "mode": "generated",
                    "confirm_provider_use": True,
                    "goal": "Create exactly one short introductory Python lesson on variables and printing. Use original examples. No student information.",
                },
            )
            result["blueprint_latency_ms"] = round((time.perf_counter() - begun) * 1000)
            result["blueprint_http_status"] = response.status_code
            if response.status_code == 201:
                cid = response.json["id"]
                client.post(
                    f"/api/learning/courses/{cid}/generation",
                    json={"confirm_provider_use": True},
                )
                begun = time.perf_counter()
                courses.run_one_job()  # exactly one lesson request
                result["lesson_latency_ms"] = round(
                    (time.perf_counter() - begun) * 1000
                )
                course = client.get(f"/api/learning/courses/{cid}").json
                result.update(
                    lessons=len(course["lessons"]),
                    planned_lessons=len(course["blueprint"]["lessons"]),
                    reserved_tokens=course["job"]["reserved_tokens"],
                    job_error=course["job"]["error"],
                )
                if course["lessons"]:
                    lesson = M["Lesson"].query.filter_by(course_id=cid).first()
                    validated = courses.validate_lesson(lesson.content)
                    result["validated_fields"] = sorted(validated)
                    result["status"] = "passed_schema_and_persistence"
            else:
                result["error_type"] = (
                    response.json.get("error") if response.is_json else "http_error"
                )
                if not available:
                    result.update(
                        status="blocked",
                        external_generation_requests=0,
                        blocked_reason="Existing Gemini contractual and Vertex configuration gate not satisfied",
                    )
        App.db.engine.dispose()
    output = (
        root
        / ".codex"
        / "verification"
        / "school-learning-hub"
        / "live-provider-results.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if result["status"] != "passed_schema_and_persistence":
        raise SystemExit(1)
