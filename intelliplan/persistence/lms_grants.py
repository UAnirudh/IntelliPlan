"""Explicit Classroom, Blackboard and Moodle credential operations."""
from datetime import datetime
from types import SimpleNamespace

from flask import current_app

from intelliplan.persistence.codec import unpack_document
from intelliplan.persistence.outlook import SQLOutlook
from time_utils import utcnow


SCHEMAS = {
    'google_classroom': ('classroom_integrations', frozenset({'access_token', 'refresh_token'}),
        frozenset({'access_token', 'refresh_token', 'token_expires_at', 'account_email', 'account_name'})),
    'blackboard': ('blackboard_integrations', frozenset({'access_token', 'refresh_token'}),
        frozenset({'access_token', 'refresh_token', 'token_expires_at', 'institution_url', 'bb_user_id', 'bb_username'})),
    'moodle': ('moodle_integrations', frozenset({'ws_token'}),
        frozenset({'ws_token', 'moodle_url', 'moodle_user_id', 'moodle_username', 'moodle_fullname'})),
}


class LMSGrant(SimpleNamespace):
    def __init__(self, provider, raw):
        import secret_box
        _, secrets, fields = SCHEMAS[provider]
        values = {key: None for key in fields | {'connected_at'}}
        values.update(unpack_document(raw))
        values.pop('_id', None)
        values.pop('_ip_encoding', None)
        for key in secrets:
            values[key] = secret_box.decrypt(values[key])
        for key in ('token_expires_at', 'connected_at'):
            if isinstance(values.get(key), str):
                values[key] = datetime.fromisoformat(values[key])
        super().__init__(**values)

    def __repr__(self):
        return f'LMSGrant(id={self.id})'


def validate_fields(provider, values, *, refresh=False):
    fields = SCHEMAS[provider][2]
    if values.keys() - fields:
        raise ValueError('Unsupported LMS connection fields')
    if refresh and values.keys() - {'access_token', 'refresh_token', 'token_expires_at'}:
        raise ValueError('Unsupported LMS refresh fields')
    secret = 'ws_token' if provider == 'moodle' else 'access_token'
    if secret not in values or not isinstance(values[secret], str) or not values[secret]:
        raise ValueError('LMS access token is required')
    if not refresh:
        origin = {'blackboard': 'institution_url', 'moodle': 'moodle_url'}.get(provider)
        if origin and (not isinstance(values.get(origin), str) or not values[origin]):
            raise ValueError('LMS institution URL is required')
    return dict(values)


def same_grant(provider, current, expected):
    fields = SCHEMAS[provider][1] | {'id', 'user_id', 'connected_at',
        'institution_url', 'moodle_url', 'account_email', 'token_expires_at'}
    return current is not None and expected is not None and all(
        getattr(current, key, None) == getattr(expected, key, None) for key in fields)


def reconnect_values(provider, previous, values):
    """Preserve omitted refresh metadata only for the same provider identity."""
    values = dict(values)
    origin = {'google_classroom': 'account_email', 'blackboard': 'institution_url'}.get(provider)
    changed = bool(previous and origin and values.get(origin) and values[origin] != getattr(previous, origin, None))
    if previous and provider == 'blackboard':
        if values.get('bb_user_id'):
            changed = changed or values['bb_user_id'] != previous.bb_user_id
        elif values.get('bb_username'):
            changed = changed or values['bb_username'] != previous.bb_username
    if changed:
        values.setdefault('refresh_token', None)
        for key in ({'account_name'} if provider == 'google_classroom' else {'bb_user_id', 'bb_username'}):
            values.setdefault(key, None)
    return values


def lms_grants(provider):
    if provider not in SCHEMAS:
        raise ValueError('Unsupported LMS connection provider')
    injected = current_app.extensions.get(f'{provider}_repository')
    if injected is not None:
        return injected
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_lms_grants import MongoLMSGrants
        return MongoLMSGrants(store, provider)
    from App import BlackboardIntegration, ClassroomIntegration, MoodleIntegration, User, db
    model = {'blackboard': BlackboardIntegration, 'google_classroom': ClassroomIntegration,
        'moodle': MoodleIntegration}[provider]
    return SQLLMSGrants(db, model, User, provider)


class SQLLMSGrants(SQLOutlook):
    def __init__(self, db, model, users, provider):
        super().__init__(db, model, users)
        self.provider = provider

    def _snapshot(self, row):
        return LMSGrant(self.provider, {column.name: getattr(row, column.name)
            for column in self.model.__table__.columns}) if row is not None else None

    def get(self, user_id):
        return self._snapshot(self.model.query.filter_by(user_id=int(user_id)).populate_existing()
            .order_by(self.model.id.desc()).first())

    def _write(self, user_id, values, expected=None):
        try:
            self._lock(user_id)
            query = self.model.query.filter_by(user_id=int(user_id)).populate_existing()
            if expected is not None:
                query = query.filter_by(id=expected.id)
            row = query.order_by(self.model.id.desc()).first()
            if expected is not None and not same_grant(self.provider, row, expected):
                self.db.session.rollback()
                return False
            if row is None:
                row = self.model(user_id=int(user_id))
                self.db.session.add(row)
            changes = reconnect_values(self.provider, row, values) if expected is None else values
            for key, value in changes.items():
                setattr(row, key, value)
            self.db.session.commit()
            return self._snapshot(row)
        except Exception:
            self.db.session.rollback()
            raise

    def connect(self, user_id, **values):
        return self._write(user_id, {**validate_fields(self.provider, values), 'connected_at': utcnow()})

    def refresh(self, user_id, expected, **values):
        if expected is None:
            raise ValueError('LMS refresh requires an existing grant')
        return self._write(user_id, validate_fields(self.provider, values, refresh=True), expected)
