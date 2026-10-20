"""Bounded live Plani checks using synthetic adult accounts and disposable storage."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run-live', action='store_true', help='Permit up to three external tutor requests')
parser.add_argument('--env-file', type=Path, help='Read existing provider settings without copying credentials')
args = parser.parse_args()
if not args.run_live:
    parser.error('--run-live is required')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / 'output/playwright/plani/live-provider.json'
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix='plani-provider-check-') as scratch:
    os.environ.update(DATABASE_URL='sqlite:///' + (Path(scratch) / 'test.db').as_posix(),
                      SECRET_KEY='disposable-plani-provider-' + os.urandom(24).hex(),
                      FLASK_ENV='testing', APP_BASE_URL='http://localhost',
                      RESEND_API_KEY='', SENTRY_DSN='', POSTHOG_API_KEY='', BILLING_ENABLED='false',
                      NOTIFICATIONS_INPROCESS_CRON='0', FOLLOWTHROUGH_INPROCESS_CRON='0')
    if args.env_file:
        from dotenv import dotenv_values
        # Provider configuration only; never import production database/session settings.
        for key, value in dotenv_values(args.env_file).items():
            if key.startswith(('GROQ_', 'GEMINI_', 'CLAUDE_', 'ANTHROPIC_', 'GOOGLE_CLOUD_')) and value:
                os.environ.setdefault(key, value)
    import App
    import ai_provider
    import policy_versions

    App.app.config['TESTING'] = True
    App.limiter.enabled = False
    report = dict(status='incomplete', data='synthetic adult account; no student data',
                  requests_maximum=3, cases=[])
    with App.app.app_context():
        App.db.create_all()
        user = App.User(email='plani-provider-check@example.test', birth_year=2000,
                        password_hash='x', ai_personalization_opt_in=False)
        App.db.session.add(user)
        App.db.session.flush()
        uid = user.id
        for doc, versions in [('terms', policy_versions.TERMS_VERSIONS),
                              ('privacy', policy_versions.PRIVACY_VERSIONS)]:
            App.db.session.add(App.PolicyAcknowledgement(
                user_id=uid, doc=doc, version=max(v['version'] for v in versions)))
        App.db.session.commit()
        report['available_chain'] = ai_provider.model_chain('standard', 'free')
        if not report['available_chain']:
            report['status'] = 'blocked_no_permitted_provider'
        else:
            cases = [
                ('interactive_slope', [{'role': 'user', 'content':
                    "I don't understand the slope in y=mx+b. Help me explore it with a slider and ask me a question."}]),
                ('answer_seeking', [{'role': 'user', 'content':
                    "Just solve 2x+3=11 for me. I don't want to do the work."}]),
                ('misconception', [{'role': 'user', 'content':
                    "For 2x+3=11, I subtracted 3 and got 2x=14. Am I on the right track?"}]),
            ]
            with App.app.test_client() as client:
                with client.session_transaction() as session:
                    session['_user_id'] = str(uid)
                    session['_fresh'] = True
                for name, messages in cases:
                    begun = time.perf_counter()
                    response = client.post('/api/tutor', json={'messages': messages})
                    data = response.get_json(silent=True) or {}
                    report['cases'].append(dict(name=name, http_status=response.status_code,
                        latency_ms=round((time.perf_counter() - begun) * 1000),
                        reply=data.get('reply', ''), error=data.get('error'),
                        contains_question='?' in data.get('reply', ''),
                        contains_interactive_exercise='type="svg-exercise"' in data.get('reply', '')))
                    print(json.dumps({'case': name, 'http_status': response.status_code}), flush=True)
                models = App.learning_models
                school = models['Organization'](name='Synthetic assessment check', kind='school',
                                                 policy={'assessment_protection': True})
                App.db.session.add(school)
                App.db.session.flush()
                App.db.session.add(models['Membership'](user_id=uid, organization_id=school.id, role='student'))
                App.db.session.commit()
                protected = client.post('/api/tutor', json={'messages': [{'role': 'user', 'content': 'Explain a derivative.'}]})
                report['assessment_protection'] = (protected.status_code == 200
                    and protected.json.get('assessment_protection') is True
                    and protected.json.get('provider_used') is False)
            report['status'] = 'responses_received' if all(
                c['http_status'] == 200 and c['reply'] for c in report['cases']) else 'provider_check_failed'
        App.db.session.remove()
        App.db.engine.dispose()
    OUTPUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'report': str(OUTPUT)}), flush=True)
    if report['status'] != 'responses_received':
        raise SystemExit(1)
