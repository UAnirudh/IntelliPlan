"""Server-side policy gates for legacy surfaces and shared AI calls."""
from flask import g, jsonify, request, session, has_request_context
import json
from flask_login import current_user
from intelliplan.learning import policy

SURFACES = {
    'tutor': ('/tutor', '/api/tutor', '/api/chatbot', '/api/plani', '/api/onboarding/chat', '/writing', '/math', '/api/writing', '/api/math', '/api/media', '/extractor', '/api/extractor', '/api/tasks/extract'),
    'study': ('/study', '/api/study-hub', '/api/deepstudy', '/api/active', '/active', '/foundations', '/api/primer', '/api/lessons', '/lessons'),
    'review': ('/learn', '/api/flashcards', '/flashcards'),
    'focus': ('/focus', '/api/focus', '/api/focus-shield', '/extension/focus', '/uploads/focus-alarm'),
    'scheduler': ('/scheduler', '/schedule', '/generate_schedule', '/api/scheduler', '/api/adjust', '/api/v1/schedule', '/extension/schedule', '/api/followthrough'),
    'canvas': ('/login/canvas', '/oauth/canvas', '/api/canvas'),
    'studentvue': ('/login/studentvue', '/api/studentvue', '/oauth/studentvue'),
    'calendar': ('/calendar', '/login/calendar-feed', '/oauth/google', '/oauth/outlook', '/oauth2callback', '/api/calendar-feed', '/api/calendar/feed'),
    'uploads': ('/notes/upload', '/api/notes/search', '/api/syllabus/import', '/api/deepstudy/ingest', '/study/extract-pdf', '/study/analyze-image', '/analyze-image', '/api/tutor/vision', '/api/assignment/notes', '/api/lessons', '/api/timetable/photo', '/oauth/onedrive', '/oauth/google-drive'),
    'notifications': ('/push', '/notifications', '/api/notifications', '/api/v1/push', '/api/reminders'),
}


def matches(path, prefix):
    return path == prefix or path.startswith(prefix + '/')


def actor():
    if (request.endpoint or '').startswith('focus_shield.'):
        from focus_shield_glue import _request_user
        return _request_user()
    if request.path.startswith('/api/v1/'):
        from intelliplan_api import _resolve_credential
        return _resolve_credential()[0]
    if request.path.startswith('/extension/'):
        from App import get_extension_user
        return get_extension_user(request.headers.get('X-Extension-Token'))
    if current_user.is_authenticated:
        return current_user
    return None


def protected_reply():
    """A deterministic concept path for institution-controlled assessment periods."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(error='invalid_request'), 400
    messages = data.get('messages', [])
    question = ' '.join(str(m.get('content', '')) for m in messages[-3:]
        if isinstance(m, dict) and m.get('role') == 'user') if isinstance(messages, list) else ''
    if 'derivative' in question.lower():
        reply = ('A derivative describes how quickly a function changes at a point. It is the limit '
                 'of the slope between nearby points as their separation approaches zero. '
                 'On a graph, think of the slope of the tangent line. '
                 'Explain what a positive tangent slope tells you about the function.')
    elif 'fraction' in question.lower():
        reply = ('A fraction describes equal parts of a whole. The denominator counts the equal parts, '
                 'and the numerator counts how many are selected. To compare fractions, express them '
                 'using the same size of part. Describe why equal denominators make comparison easier.')
    else:
        reply = ('Your institution has enabled assessment protection. Name the underlying concept '
                 'without pasting the assessed question, explain what you already understand, '
                 'and identify the step you find confusing. The reviewed Foundations practice '
                 'activities provide explanations and independent checks.')
    return jsonify(reply=reply, assessment_protection=True, provider_used=False)


def install(app):
    @app.before_request
    def enforce():
        if request.method == 'OPTIONS' or request.endpoint == 'static':
            return None
        if request.path.endswith('/disconnect') or request.path == '/api/calendar/feed/revoke':
            return None  # Existing authenticated handlers must allow credential revocation.
        keys = [key for key, paths in SURFACES.items() if any(matches(request.path, p) for p in paths)]
        if request.path == '/oauth/outlook/callback' and session.get('microsoft_oauth_purpose') == 'onedrive':
            keys = ['uploads']
        if request.path in {'/oauth2callback', '/oauth/google/callback'}:
            purpose = session.get('oauth_purpose', 'calendar')
            keys = ['uploads'] if purpose == 'drive' else ([] if purpose == 'login' else ['calendar'])
        grade_data = request.path in {'/grades/data', '/gradebook/detail', '/api/v1/grades'}
        live_data = grade_data or request.path in {'/live', '/gradebook', '/courses', '/tasks/unified', '/missing/data', '/api/v1/courses', '/api/v1/assignments', '/extension/tasks', '/extension/grades'}
        if request.path.startswith('/notes/') and request.path.rsplit('/', 1)[-1] in {'study', 'quiz', 'summarize'}:
            keys.append('study')
        generated_action = (request.path.startswith('/api/deepstudy/') or
            request.path.startswith('/api/lessons/') or
            request.path in {'/study/generate', '/study/evaluate', '/analyze-image', '/study/analyze-image',
                             '/study/transcribe', '/api/lessons'} or
            (request.path.startswith('/notes/') and request.path.rsplit('/', 1)[-1] in {'study', 'quiz', 'summarize'}))
        if not keys and not live_data:
            return None
        try:
            user = actor()
            g.learning_actor = user
            if not user:
                if ('tutor' in keys or generated_action) and request.method not in {'GET', 'HEAD'}:
                    return jsonify(error='authentication_required', message='Sign in to use AI assistance so your current policy can be checked.'), 401
                return None
            if live_data:
                from App import LinkedAccount
                linked = LinkedAccount.query.filter_by(user_id=user.id, is_active=True).first()
                if grade_data:
                    raw = getattr(user, 'lms_preferences', None) or '{}'
                    prefs = json.loads(raw) if isinstance(raw, str) else raw
                    source = (prefs.get('grade_source') or 'active').strip().lower()
                    if source in {'canvas', 'studentvue', 'schoology', 'hac'}:
                        chosen = LinkedAccount.query.filter_by(user_id=user.id, login_type=source).order_by(
                            LinkedAccount.is_active.desc(), LinkedAccount.id.desc()).first()
                        linked = chosen or linked
                if linked and linked.login_type in {'canvas', 'studentvue'}:
                    keys.append(linked.login_type)
                elif linked and linked.login_type == 'calendar_feed':
                    keys.append('calendar')
            for key in keys:
                allowed, reason = policy.decision(user, key)
                if not allowed:
                    return jsonify(error=reason, feature=key, message='Your institution has restricted this capability.'), 403
            settings = policy.effective(user)
            if settings['assessment_protection']:
                if request.path == '/api/tutor' and request.method == 'POST':
                    return protected_reply()
                if ('tutor' in keys or generated_action) and request.method not in {'GET', 'HEAD'}:
                    return jsonify(error='assessment_protection', message='Use the protected tutor for concept support.'), 403
            if request.path in {'/api/tutor/voice', '/api/tutor/speak', '/api/tutor/vision', '/api/media/generate',
                                '/study/transcribe', '/study/analyze-image'}:
                if settings['providers'] != policy.PROVIDERS or not settings['third_party_processing']:
                    return jsonify(error='provider_restricted'), 403
        except Exception:
            return jsonify(error='policy_unavailable', message='Policy could not be checked. Try again later.'), 503
        return None


def filter_providers(chain):
    from intelliplan.learning.courses import provider_user
    user = provider_user.get()
    if user is None and has_request_context():
        user = getattr(g, 'learning_actor', None) or current_user
    if user is None or not getattr(user, 'is_authenticated', False):
        return chain
    try:
        permitted = policy.permitted_providers(user)
    except Exception:
        permitted = set()
    return [step for step in chain if step[0] in permitted]


def permitted_processing(provider):
    """Shared media gate; providers outside the text registry require unrestricted institutional processing."""
    from intelliplan.learning.courses import provider_user
    user = provider_user.get()
    if user is None and has_request_context():
        user = getattr(g, 'learning_actor', None) or actor()
    if user is None or not getattr(user, 'is_authenticated', False):
        return True
    try:
        permitted = policy.permitted_providers(user)
        return provider in permitted if provider in policy.PROVIDERS else permitted == policy.PROVIDERS
    except Exception:
        return False
