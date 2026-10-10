"""One policy decision for web requests, direct APIs, and generation workers.

Restrictions are intersections, never grants. No subscription overrides beta
eligibility. Missing experimental flags and failed policy reads deny access.
"""
from dataclasses import dataclass
from flask import g, has_request_context


@dataclass(frozen=True)
class Feature:
    maturity: str
    limitation: str = ''
    flag: str | None = None
    future_entitlement: str | None = None


FEATURES = {
    'tutor': Feature('stable', 'Generated replies may be incorrect; practice evidence is limited.'),
    'study': Feature('stable', 'Self-reported completion is distinct from scored practice.'),
    'review': Feature('stable', 'Review history is not a validated mastery measurement.'),
    'focus': Feature('stable', 'Website blocking requires the optional extension; emergency exit remains available.'),
    'scheduler': Feature('stable'),
    'canvas': Feature('stable'),
    'studentvue': Feature('stable', 'Legacy connection; district permission and live reliability require separate verification.'),
    'calendar': Feature('stable'),
    'uploads': Feature('stable'),
    'notifications': Feature('stable'),
    'course_generator': Feature('admin_only_beta', 'Administrator evaluation only. Content needs human review.',
                                'school_course_generator', 'premium'),
    'generated_content': Feature('admin_only_beta', 'Model output is unverified until reviewed.',
                                 'school_course_generator', 'premium'),
    'video_generation': Feature('internal_alpha', 'No rendering provider is connected.', 'school_video_generation'),
}
PROVIDERS = frozenset({'gemini', 'groq', 'claude'})


def models():
    from App import learning_models
    return learning_models


def validate_policy(value):
    if not isinstance(value, dict):
        raise ValueError('Policy must be an object.')
    booleans = set(FEATURES) | {'allow_beta', 'third_party_processing', 'assessment_protection'}
    unknown = set(value) - booleans - {'providers', 'max_generation_tokens'}
    if unknown:
        raise ValueError('Unknown policy setting: ' + ', '.join(sorted(unknown)))
    for key in booleans & value.keys():
        if type(value[key]) is not bool:
            raise ValueError(key + ' must be true or false.')
    if 'providers' in value:
        if not isinstance(value['providers'], list) or any(p not in PROVIDERS for p in value['providers']):
            raise ValueError('Providers must be a list of supported provider names.')
    if 'max_generation_tokens' in value:
        n = value['max_generation_tokens']
        if type(n) is not int or not 1000 <= n <= 100000:
            raise ValueError('Generation budget must be between 1000 and 100000 tokens.')
    return dict(value)


def organizations(user):
    if not getattr(user, 'is_authenticated', False):
        return []
    M = models()
    rows = M['Membership'].query.filter_by(user_id=user.id).all()
    result = {}
    for row in rows:
        org = M['Organization'].query.get(row.organization_id)
        while org and org.id not in result:
            result[org.id] = org
            org = M['Organization'].query.get(org.parent_id) if org.parent_id else None
    return list(result.values())


def effective(user):
    cache_key = ('learning_policy', getattr(user, 'id', None))
    if has_request_context() and getattr(g, '_learning_policy_key', None) == cache_key:
        return g._learning_policy
    rows = organizations(user)
    policy = {key: True for key in FEATURES}
    policy.update(allow_beta=True, third_party_processing=True, assessment_protection=False,
                  providers=set(PROVIDERS), max_generation_tokens=24000)
    for org in rows:
        data = validate_policy(org.policy)
        for key, value in data.items():
            if key == 'providers':
                policy[key] &= set(value)
            elif key == 'max_generation_tokens':
                policy[key] = min(policy[key], value)
            elif key == 'assessment_protection':
                policy[key] |= value
            else:
                policy[key] &= value
    if has_request_context():
        g._learning_policy_key, g._learning_policy = cache_key, policy
    return policy


def invalidate_cache():
    """Workers must re-evaluate policies even when invoked from a request context."""
    if has_request_context():
        g.pop('_learning_policy_key', None)
        g.pop('_learning_policy', None)


def administers(user, organization_id):
    from App import is_admin
    if is_admin(user):
        return True
    if not getattr(user, 'is_authenticated', False):
        return False
    M = models()
    org = M['Organization'].query.get(organization_id)
    if not org:
        return False
    scope = [org.id] + ([org.parent_id] if org.parent_id else [])
    return M['Membership'].query.filter(M['Membership'].user_id == user.id,
                                       M['Membership'].organization_id.in_(scope),
                                       M['Membership'].role == 'admin').first() is not None


def is_learning_admin(user):
    from App import is_admin
    if is_admin(user):
        return True
    return bool(getattr(user, 'is_authenticated', False) and
                models()['Membership'].query.filter_by(user_id=user.id, role='admin').first())


def decision(user, key):
    from App import FeatureFlag
    feature = FEATURES.get(key)
    if feature is None:
        return False, 'unknown_feature'
    try:
        policy = effective(user)
        if not policy[key]:
            return False, 'institution_policy'
        if feature.maturity != 'stable' and not policy['allow_beta']:
            return False, 'beta_prohibited'
        if feature.maturity in {'admin_only_beta', 'internal_alpha'} and not is_learning_admin(user):
            return False, 'administrator_only_beta'
        if feature.flag:
            flag = FeatureFlag.query.filter_by(key=feature.flag).first()
            if not flag or not flag.enabled:
                return False, 'feature_disabled'
        # Video stays unavailable even if an operator toggles an old flag.
        if key == 'video_generation':
            return False, 'provider_not_integrated'
        return True, 'allowed'
    except Exception:
        return False, 'policy_unavailable'


def permitted_providers(user):
    policy = effective(user)
    return policy['providers'] if policy['third_party_processing'] and not policy['assessment_protection'] else set()
